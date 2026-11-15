"""The network-namespace egress proxy: every outbound connection, vetted.

A sandbox may talk to the network — some projects need it — but it talks
through here, and here the rules are:

- **Domain allowlists.** A per-project policy names the hosts (or suffixes)
  a sandbox may reach. Anything else is refused, and the refusal is a 403 the
  caller can read.
- **The metadata service is never a destination.** `169.254.169.254` and its
  IPv6 twin are blocked by address, not by name, so a DNS record that
  resolves to the metadata service is a refusal, not a request.
- **Private ranges are refused** — RFC1918, loopback, link-local, unique
  local, and the v6 equivalents — unless the policy explicitly admits one, so
  a sandbox cannot pivot to the host's own services by knowing an IP.
- **Every hop is checked, not just the first.** A redirect is a new
  destination: the proxy validates the `Location` target before it follows
  it, hop after hop, so an allowed host that redirects to the metadata
  service ends in a refusal, not in a read of the host's credentials.
- **Raw sockets are the same question.** A CONNECT request — what a browser
  or `curl` uses for HTTPS — gets the identical check on the host, and the
  connection is refused before the tunnel opens.

Every decision lands in `audit_log`: allowed, refused, and why. An operator
reading the audit can answer "what did this sandbox talk to" without reading
a packet capture.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

from clio.observability.logger import get_logger

logger = get_logger("clio.sandbox.egress")

#: The cloud metadata service, v4 and v6. Never an allowed destination.
METADATA_ADDRESSES = frozenset({"169.254.169.254", "fd00:ec2::254", "100.100.100.200"})

#: Redirects the proxy will follow itself. Bounded, because an infinite
#: redirect loop is a denial of service a sandbox should not be able to mount.
MAX_REDIRECT_HOPS = 5


@dataclass
class EgressPolicy:
    """One project's network rules."""

    allowed_hosts: list[str] = field(default_factory=list)
    allow_private: bool = False
    allow_metadata: bool = False
    #: Hosts matched by suffix (`example.com` admits `api.example.com`).
    suffix_match: bool = True

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> EgressPolicy:
        data = dict(data or {})
        return cls(
            allowed_hosts=[str(h) for h in data.get("allowed_hosts", [])],
            allow_private=bool(data.get("allow_private", False)),
            allow_metadata=bool(data.get("allow_metadata", False)),
            suffix_match=bool(data.get("suffix_match", True)),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "allowed_hosts": list(self.allowed_hosts),
            "allow_private": self.allow_private,
            "allow_metadata": self.allow_metadata,
            "suffix_match": self.suffix_match,
        }

    def host_allowed(self, host: str) -> bool:
        host = (host or "").strip().lower().rstrip(".")
        if not host:
            return False
        for entry in self.allowed_hosts:
            entry = entry.strip().lower().rstrip(".")
            if not entry:
                continue
            if host == entry:
                return True
            if self.suffix_match and host.endswith("." + entry):
                return True
        return False


@dataclass
class EgressDecision:
    allowed: bool
    reason: str
    host: str = ""
    addresses: list[str] = field(default_factory=list)


def _address_blocked(address: str, policy: EgressPolicy) -> str | None:
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return f"unparseable address {address!r}"
    if str(ip) in METADATA_ADDRESSES:
        return None if policy.allow_metadata else "cloud metadata service"
    if policy.allow_private:
        return None
    if ip.is_loopback:
        return "loopback"
    if ip.is_link_local:
        return "link-local"
    if ip.is_private:
        return "private range (RFC1918/ULA)"
    if ip.is_reserved or ip.is_multicast or ip.is_unspecified:
        return "reserved range"
    return None


class EgressGuard:
    """The policy plus the DNS check — shared by the proxy and by tools."""

    def __init__(self, policy: EgressPolicy, audit: Any = None, *, sandbox_id: str = "",
                 project_id: str | None = None) -> None:
        self.policy = policy
        self.audit = audit
        self.sandbox_id = sandbox_id
        self.project_id = project_id

    def resolve(self, host: str) -> list[str]:
        try:
            infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
        except socket.gaierror as exc:
            raise EgressBlocked(f"DNS resolution failed for {host}: {exc}") from exc
        return sorted({info[4][0] for info in infos})

    def check(self, host: str, *, resolve: bool = True) -> EgressDecision:
        """One hop's check: the name, then every address it resolves to.

        Every address is checked, not the first: a host answering with one
        public and one private address must not pass because the resolver
        handed the public one first.
        """
        host = (host or "").strip().lower()
        if not host:
            return EgressDecision(False, "no host given")
        if not self.policy.host_allowed(host):
            return EgressDecision(False, f"host {host!r} is not on this project's egress allowlist", host)
        addresses: list[str] = []
        if resolve:
            try:
                addresses = self.resolve(host)
            except EgressBlocked as exc:
                return EgressDecision(False, str(exc), host)
            for address in addresses:
                blocked = _address_blocked(address, self.policy)
                if blocked is not None:
                    return EgressDecision(
                        False, f"{host} resolves to {address} ({blocked}), which is never a destination",
                        host, addresses,
                    )
        return EgressDecision(True, "allowed", host, addresses)

    async def audit_connection(self, decision: EgressDecision, *, url: str = "",
                               method: str = "", hop: int = 0) -> None:
        if self.audit is None:
            return
        try:
            await self.audit.append(
                actor="egress",
                category="network",
                summary=(
                    f"egress {'allowed' if decision.allowed else 'blocked'} "
                    f"{decision.host or url} ({decision.reason})"
                ),
                details={
                    "sandbox_id": self.sandbox_id,
                    "project_id": self.project_id,
                    "host": decision.host,
                    "addresses": decision.addresses,
                    "url": url,
                    "method": method,
                    "hop": hop,
                    "allowed": decision.allowed,
                    "reason": decision.reason,
                },
            )
        except Exception:
            logger.exception("egress.audit_failed")

    async def check_url_chain(self, url: str, *, follow: Any = None,
                              resolve: bool = True) -> EgressDecision:
        """Validate one URL and, when asked, every redirect hop it points at.

        `follow` is an async callable answering `(status, location_headers)`
        for a URL — the proxy and the http tool each pass their own. The loop
        is what makes "across every redirect hop" true rather than aspirational:
        each Location is resolved and checked before it is followed, and the
        fifth hop is where a chain that will not end is cut. `resolve=False`
        checks names against the allowlist without DNS, which is the right
        mode when the caller resolves and validates addresses itself.
        """
        current = url
        for hop in range(MAX_REDIRECT_HOPS + 1):
            parts = urlsplit(current)
            decision = self.check(parts.hostname or "", resolve=resolve)
            await self.audit_connection(decision, url=current, hop=hop)
            if not decision.allowed:
                return decision
            if follow is None:
                return decision
            status, location = await follow(current)
            if not (status in (301, 302, 303, 307, 308) and location):
                return decision
            if hop == MAX_REDIRECT_HOPS:
                # The last hop was followed and the chain is still pointing
                # somewhere: a chain that will not end is a denial of service
                # and is cut here, with the next target named.
                return EgressDecision(
                    False,
                    f"too many redirect hops (limit {MAX_REDIRECT_HOPS})",
                    urlsplit(str(location)).hostname or decision.host,
                )
            current = str(location)
            if "://" not in current:
                base = urlsplit(url)
                current = f"{base.scheme}://{base.netloc}{current}"
        return EgressDecision(False, "too many redirect hops", urlsplit(url).hostname or "")


class EgressBlocked(Exception):
    """A destination the policy refuses."""


class EgressProxy:
    """A minimal forward proxy: absolute-URI requests and CONNECT tunnels.

    It runs on loopback inside the host namespace and the sandbox's network
    namespace routes through it (`CLIO_EGRESS_PROXY`), so a sandbox process
    that wants the network has exactly one path out, and that path is here.
    The proxy does not implement TLS interception; it validates the CONNECT
    target before the tunnel opens and the request target before it forwards.
    """

    def __init__(self, guard: EgressGuard, *, host: str = "127.0.0.1", port: int = 0) -> None:
        self.guard = guard
        self.host = host
        self.port = port
        self._server: asyncio.AbstractServer | None = None
        self._tasks: set[asyncio.Task] = set()

    @property
    def address(self) -> str:
        return f"http://{self.host}:{self.port}"

    async def start(self) -> EgressProxy:
        self._server = await asyncio.start_server(self._handle_client, self.host, self.port)
        self.port = self._server.sockets[0].getsockname()[1]
        logger.info("egress.proxy_started", address=self.address, sandbox=self.guard.sandbox_id)
        return self

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        for task in list(self._tasks):
            task.cancel()

    async def _handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        task = asyncio.current_task()
        if task is not None:
            self._tasks.add(task)
        try:
            request_line = await asyncio.wait_for(reader.readline(), timeout=10)
            if not request_line:
                return
            line = request_line.decode("latin-1").strip()
            parts = line.split()
            if len(parts) < 2:
                await self._refuse(writer, 400, "malformed request")
                return
            method, target = parts[0].upper(), parts[1]
            if method == "CONNECT":
                await self._handle_connect(target, reader, writer)
            else:
                await self._handle_forward(method, target, reader, writer)
        except TimeoutError:
            pass
        except Exception:
            logger.exception("egress.proxy_client_failed")
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass
            if task is not None:
                self._tasks.discard(task)

    async def _handle_connect(self, target: str, reader: asyncio.StreamReader,
                              writer: asyncio.StreamWriter) -> None:
        host, _, port_s = target.partition(":")
        decision = self.guard.check(host)
        await self.guard.audit_connection(decision, url=f"{host}:{port_s}", method="CONNECT")
        if not decision.allowed:
            await self._refuse(writer, 403, decision.reason)
            return
        try:
            remote_reader, remote_writer = await asyncio.open_connection(host, int(port_s or "443"))
        except OSError as exc:
            await self._refuse(writer, 502, f"upstream connect failed: {exc}")
            return
        writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
        await writer.drain()
        await asyncio.gather(
            self._pump(reader, remote_writer),
            self._pump(remote_reader, writer),
            return_exceptions=True,
        )

    @staticmethod
    async def _pump(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            while True:
                chunk = await reader.read(65536)
                if not chunk:
                    break
                writer.write(chunk)
                await writer.drain()
        except Exception:
            pass
        finally:
            try:
                writer.close()
            except Exception:
                pass

    async def _handle_forward(self, method: str, target: str, reader: asyncio.StreamReader,
                              writer: asyncio.StreamWriter) -> None:
        parts = urlsplit(target)
        host = parts.hostname or ""
        decision = self.guard.check(host)
        await self.guard.audit_connection(decision, url=target, method=method)
        if not decision.allowed:
            await self._refuse(writer, 403, decision.reason)
            return
        # Drain headers, forward the request with headers intact. The body is
        # forwarded for methods that carry one; the relay is byte-for-byte and
        # the validation happened before anything was sent upstream.
        headers: list[bytes] = []
        while True:
            header = await asyncio.wait_for(reader.readline(), timeout=10)
            if header in (b"\r\n", b"\n", b""):
                break
            headers.append(header)
        port = parts.port or (443 if parts.scheme == "https" else 80)
        try:
            remote_reader, remote_writer = await asyncio.open_connection(host, port)
        except OSError as exc:
            await self._refuse(writer, 502, f"upstream connect failed: {exc}")
            return
        path = parts.path or "/"
        if parts.query:
            path += "?" + parts.query
        remote_writer.write(f"{method} {path} HTTP/1.1\r\n".encode("latin-1"))
        for header in headers:
            if header.lower().startswith((b"proxy-connection", b"connection")):
                continue
            remote_writer.write(header)
        remote_writer.write(b"\r\n")
        await remote_writer.drain()
        await asyncio.gather(
            self._pump(reader, remote_writer),
            self._pump(remote_reader, writer),
            return_exceptions=True,
        )

    @staticmethod
    async def _refuse(writer: asyncio.StreamWriter, status: int, reason: str) -> None:
        body = f"egress blocked: {reason}\n".encode()
        writer.write(
            f"HTTP/1.1 {status} Blocked\r\nContent-Length: {len(body)}\r\n"
            f"Content-Type: text/plain\r\n\r\n".encode("latin-1")
        )
        writer.write(body)
        await writer.drain()


__all__ = [
    "EgressBlocked",
    "EgressDecision",
    "EgressGuard",
    "EgressPolicy",
    "EgressProxy",
    "MAX_REDIRECT_HOPS",
    "METADATA_ADDRESSES",
]
