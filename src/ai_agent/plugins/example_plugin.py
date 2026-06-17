"""
Example plugin demonstrating the Clio-Agent-1 plugin system
"""

from . import hookimpl


class ExamplePlugin:
    """
    Example plugin that logs all commands and responses
    """
    
    def __init__(self):
        self.command_count = 0
        self.error_count = 0
    
    @hookimpl
    def clio_agent_initialize(self, config: dict) -> None:
        """Called when Clio-Agent-1 initializes"""
        print(f"[ExamplePlugin] Initialized with config: {config}")
    
    @hookimpl
    def clio_agent_pre_execute(self, command: str, context: dict) -> str:
        """Log commands before execution"""
        self.command_count += 1
        print(f"[ExamplePlugin] Pre-execute: {command[:50]}...")
        return command
    
    @hookimpl
    def clio_agent_post_execute(self, command: str, result: dict, context: dict) -> None:
        """Log results after execution"""
        exit_code = result.get('exit_code', 'unknown')
        print(f"[ExamplePlugin] Post-execute: exit_code={exit_code}")
    
    @hookimpl
    def clio_agent_pre_phase(self, phase: str, context: dict) -> None:
        """Log phase start"""
        print(f"[ExamplePlugin] Starting phase: {phase}")
    
    @hookimpl
    def clio_agent_on_error(self, error: Exception, context: dict) -> bool:
        """Log errors"""
        self.error_count += 1
        print(f"[ExamplePlugin] Error occurred: {error}")
        return False  # Don't handle, just log
    
    @hookimpl
    def clio_agent_get_commands(self) -> list:
        """Register custom commands"""
        return [
            ("plugin-stats", self.show_stats, "Show plugin statistics"),
        ]
    
    def show_stats(self):
        """Show plugin statistics"""
        print(f"Commands executed: {self.command_count}")
        print(f"Errors encountered: {self.error_count}")


# Create plugin instance
plugin = ExamplePlugin()
