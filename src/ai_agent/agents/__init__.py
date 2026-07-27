"""
AI Agent System for Background Tasks

Provides long-running agents that execute in background while main conversation continues.
"""

import asyncio
import logging
import uuid
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional, Dict, Any, List, Callable, AsyncGenerator
from collections import deque

logger = logging.getLogger(__name__)


class AgentStatus(Enum):
    """Agent execution status."""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class AgentTask:
    """A task for an agent to execute."""
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    name: str = ""
    description: str = ""
    agent_type: str = ""
    input_data: Dict[str, Any] = field(default_factory=dict)
    status: AgentStatus = AgentStatus.PENDING
    result: Optional[Any] = None
    error: Optional[str] = None
    created_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    completed_at: Optional[float] = None
    progress: float = 0.0
    progress_message: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class AgentConfig:
    """Agent configuration."""
    name: str = ""
    description: str = ""
    system_prompt: str = ""
    max_iterations: int = 50
    timeout_seconds: int = 300
    tools: List[str] = field(default_factory=list)
    llm_config: Optional[Dict[str, Any]] = None
    working_dir: Optional[str] = None


class BaseAgent(ABC):
    """Base class for all agents."""
    
    def __init__(self, config: AgentConfig):
        self.config = config
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._progress_callback: Optional[Callable[[float, str], None]] = None
        self._result_callback: Optional[Callable[[Any], None]] = None
        self._error_callback: Optional[Callable[[Exception], None]] = None
    
    @property
    @abstractmethod
    def agent_type(self) -> str:
        """Unique agent type identifier."""
        pass
    
    @property
    @abstractmethod
    def capabilities(self) -> List[str]:
        """List of agent capabilities."""
        pass
    
    def set_progress_callback(self, callback: Callable[[float, str], None]):
        """Set progress callback."""
        self._progress_callback = callback
    
    def set_result_callback(self, callback: Callable[[Any], None]):
        """Set result callback."""
        self._result_callback = callback
    
    def set_error_callback(self, callback: Callable[[Exception], None]):
        """Set error callback."""
        self._error_callback = callback
    
    def _update_progress(self, progress: float, message: str):
        """Update progress."""
        if self._progress_callback:
            self._progress_callback(progress, message)
    
    @abstractmethod
    async def execute(self, task: AgentTask) -> Any:
        """Execute the agent task. Returns result."""
        pass
    
    async def run_task(self, task: AgentTask) -> AgentTask:
        """Run a task with full lifecycle management."""
        task.status = AgentStatus.RUNNING
        task.started_at = time.time()
        self._running = True
        
        try:
            result = await self.execute(task)
            task.result = result
            task.status = AgentStatus.COMPLETED
            task.progress = 1.0
            task.progress_message = "Completed"
            
            if self._result_callback:
                self._result_callback(result)
            
        except asyncio.CancelledError:
            task.status = AgentStatus.CANCELLED
            task.error = "Task cancelled"
            raise
        
        except Exception as e:
            task.status = AgentStatus.FAILED
            task.error = str(e)
            
            if self._error_callback:
                self._error_callback(e)
            
            logger.error(f"Agent {self.agent_type} task failed: {e}")
        
        finally:
            task.completed_at = time.time()
            self._running = False
        
        return task
    
    def cancel(self):
        """Cancel the running task."""
        if self._task and not self._task.done():
            self._task.cancel()
    
    @property
    def is_running(self) -> bool:
        return self._running


class CodingAgent(BaseAgent):
    """Agent specialized for coding tasks."""
    
    @property
    def agent_type(self) -> str:
        return "coding"
    
    @property
    def capabilities(self) -> List[str]:
        return [
            "code_generation",
            "code_editing",
            "debugging",
            "refactoring",
            "testing",
            "file_operations",
            "git_operations",
            "code_review"
        ]
    
    async def execute(self, task: AgentTask) -> Any:
        """Execute coding task."""
        # This would integrate with the existing coding agent
        # For now, return a placeholder
        self._update_progress(0.1, "Analyzing task...")
        await asyncio.sleep(0.5)
        
        self._update_progress(0.3, "Planning implementation...")
        await asyncio.sleep(0.5)
        
        self._update_progress(0.6, "Writing code...")
        await asyncio.sleep(1.0)
        
        self._update_progress(0.9, "Running tests...")
        await asyncio.sleep(0.5)
        
        self._update_progress(1.0, "Done")
        
        return {
            "status": "completed",
            "files_modified": [],
            "summary": f"Completed coding task: {task.description}"
        }


class ResearchAgent(BaseAgent):
    """Agent specialized for research tasks."""
    
    @property
    def agent_type(self) -> str:
        return "research"
    
    @property
    def capabilities(self) -> List[str]:
        return [
            "web_search",
            "code_search",
            "documentation_lookup",
            "api_research",
            "technology_evaluation",
            "comparison_analysis"
        ]
    
    async def execute(self, task: AgentTask) -> Any:
        """Execute research task."""
        self._update_progress(0.1, "Gathering information...")
        await asyncio.sleep(0.5)
        
        self._update_progress(0.5, "Analyzing sources...")
        await asyncio.sleep(1.0)
        
        self._update_progress(0.8, "Synthesizing findings...")
        await asyncio.sleep(0.5)
        
        self._update_progress(1.0, "Done")
        
        return {
            "status": "completed",
            "findings": [],
            "summary": f"Research completed: {task.description}"
        }


class ReviewAgent(BaseAgent):
    """Agent specialized for code review."""
    
    @property
    def agent_type(self) -> str:
        return "review"
    
    @property
    def capabilities(self) -> List[str]:
        return [
            "code_review",
            "security_audit",
            "performance_analysis",
            "best_practices_check",
            "style_check",
            "dependency_analysis"
        ]
    
    async def execute(self, task: AgentTask) -> Any:
        """Execute review task."""
        self._update_progress(0.1, "Analyzing code...")
        await asyncio.sleep(0.5)
        
        self._update_progress(0.5, "Checking for issues...")
        await asyncio.sleep(1.0)
        
        self._update_progress(0.8, "Generating report...")
        await asyncio.sleep(0.5)
        
        self._update_progress(1.0, "Done")
        
        return {
            "status": "completed",
            "issues": [],
            "summary": f"Review completed: {task.description}"
        }


class ArchitectAgent(BaseAgent):
    """Agent specialized for system design."""
    
    @property
    def agent_type(self) -> str:
        return "architect"
    
    @property
    def capabilities(self) -> List[str]:
        return [
            "system_design",
            "architecture_review",
            "tech_stack_selection",
            "scalability_planning",
            "api_design",
            "database_design"
        ]
    
    async def execute(self, task: AgentTask) -> Any:
        """Execute architecture task."""
        self._update_progress(0.1, "Analyzing requirements...")
        await asyncio.sleep(0.5)
        
        self._update_progress(0.4, "Designing architecture...")
        await asyncio.sleep(1.0)
        
        self._update_progress(0.8, "Creating documentation...")
        await asyncio.sleep(0.5)
        
        self._update_progress(1.0, "Done")
        
        return {
            "status": "completed",
            "design": {},
            "summary": f"Architecture design completed: {task.description}"
        }


class AgentManager:
    """Manages multiple agents and their tasks."""
    
    AGENT_TYPES = {
        "coding": CodingAgent,
        "research": ResearchAgent,
        "review": ReviewAgent,
        "architect": ArchitectAgent,
    }
    
    def __init__(self, max_parallel: int = 4):
        self.max_parallel = max_parallel
        self._agents: Dict[str, BaseAgent] = {}
        self._tasks: Dict[str, AgentTask] = {}
        self._running_tasks: Dict[str, asyncio.Task] = {}
        self._semaphore: asyncio.Semaphore = None
        self._task_queue: asyncio.Queue = asyncio.Queue()
        self._worker_tasks: List[asyncio.Task] = []
        self._running = False
    
    def get_agent(self, agent_type: str, config: AgentConfig = None) -> BaseAgent:
        """Get or create an agent."""
        if agent_type not in self._agents:
            if agent_type not in self.AGENT_TYPES:
                raise ValueError(f"Unknown agent type: {agent_type}")
            
            if config is None:
                config = AgentConfig()
            
            agent_class = self.AGENT_TYPES[agent_type]
            self._agents[agent_type] = agent_class(config)
        
        return self._agents[agent_type]
    
    async def start(self):
        """Start the agent manager."""
        if self._running:
            return
        
        self._running = True
        self._semaphore = asyncio.Semaphore(self.max_parallel)
        
        # Start worker tasks
        for i in range(self.max_parallel):
            task = asyncio.create_task(self._worker(f"worker-{i}"))
            self._worker_tasks.append(task)
        
        logger.info(f"Agent manager started with {self.max_parallel} workers")
    
    async def stop(self):
        """Stop the agent manager."""
        self._running = False
        
        # Cancel all running tasks
        for task in self._running_tasks.values():
            task.cancel()
        
        # Wait for workers
        for task in self._worker_tasks:
            task.cancel()
        
        await asyncio.gather(*self._worker_tasks, return_exceptions=True)
        self._worker_tasks.clear()
        
        logger.info("Agent manager stopped")
    
    async def _worker(self, name: str):
        """Worker that processes tasks from queue."""
        while self._running:
            try:
                # Wait for task with timeout
                task_data = await asyncio.wait_for(
                    self._task_queue.get(),
                    timeout=1.0
                )
                
                agent_type, task, callbacks = task_data
                agent = self.get_agent(agent_type)
                
                # Set callbacks
                if callbacks.get("progress"):
                    agent.set_progress_callback(callbacks["progress"])
                if callbacks.get("result"):
                    agent.set_result_callback(callbacks["result"])
                if callbacks.get("error"):
                    agent.set_error_callback(callbacks["error"])
                
                # Run task with semaphore
                async with self._semaphore:
                    run_task = asyncio.create_task(agent.run_task(task))
                    self._running_tasks[task.id] = run_task
                    
                    try:
                        await run_task
                    finally:
                        self._running_tasks.pop(task.id, None)
                
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Worker {name} error: {e}")
    
    def spawn_agent(
        self,
        agent_type: str,
        task: AgentTask,
        progress_callback: Callable[[float, str], None] = None,
        result_callback: Callable[[Any], None] = None,
        error_callback: Callable[[Exception], None] = None
    ) -> str:
        """Spawn an agent task (non-blocking)."""
        self._tasks[task.id] = task
        
        callbacks = {}
        if progress_callback:
            callbacks["progress"] = progress_callback
        if result_callback:
            callbacks["result"] = result_callback
        if error_callback:
            callbacks["error"] = error_callback
        
        # Queue the task
        self._task_queue.put_nowait((agent_type, task, callbacks))
        
        return task.id
    
    async def spawn_and_wait(
        self,
        agent_type: str,
        task: AgentTask,
        progress_callback: Callable[[float, str], None] = None,
        timeout: float = None
    ) -> AgentTask:
        """Spawn agent and wait for completion."""
        task_id = self.spawn_agent(agent_type, task, progress_callback)
        
        # Wait for completion
        while task.status in (AgentStatus.PENDING, AgentStatus.RUNNING):
            await asyncio.sleep(0.1)
            if timeout:
                timeout -= 0.1
                if timeout <= 0:
                    task.status = AgentStatus.FAILED
                    task.error = "Timeout"
                    break
        
        return task
    
    def get_task(self, task_id: str) -> Optional[AgentTask]:
        """Get task by ID."""
        return self._tasks.get(task_id)
    
    def list_tasks(self, status: AgentStatus = None) -> List[AgentTask]:
        """List tasks, optionally filtered by status."""
        tasks = list(self._tasks.values())
        if status:
            tasks = [t for t in tasks if t.status == status]
        return tasks
    
    def cancel_task(self, task_id: str) -> bool:
        """Cancel a running task."""
        if task_id in self._running_tasks:
            self._running_tasks[task_id].cancel()
            return True
        return False
    
    def get_running_count(self) -> int:
        """Get number of running tasks."""
        return len(self._running_tasks)


def create_agent_manager(max_parallel: int = 4) -> AgentManager:
    """Factory function to create agent manager."""
    return AgentManager(max_parallel)