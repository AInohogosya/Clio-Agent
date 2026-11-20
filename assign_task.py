import asyncio, json, sys
import websockets

async def main(text):
    uri = "ws://127.0.0.1:8710"
    async with websockets.connect(uri) as ws:
        await ws.send(json.dumps({"token": ""}))
        try:
            hello = await asyncio.wait_for(ws.recv(), timeout=5)
            print("HELLO:", str(hello)[:300])
        except Exception as e:
            print("no hello reply:", e)
        await ws.send(json.dumps({"type": "message", "text": text}))
        # Collect any immediate frames for a short window.
        end = asyncio.get_event_loop().time() + 8
        while asyncio.get_event_loop().time() < end:
            try:
                msg = await asyncio.wait_for(ws.recv(), timeout=max(0.5, end - asyncio.get_event_loop().time()))
                print("FRAME:", str(msg)[:500])
                if '"type": "done"' in str(msg) or '"type":"done"' in str(msg):
                    break
            except asyncio.TimeoutError:
                break
        print("SENT")

if __name__ == "__main__":
    asyncio.run(main(sys.argv[1]))
