"""
Example plugin demonstrating the VEXIS plugin system
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
    def vexis_initialize(self, config: dict) -> None:
        """Called when VEXIS initializes"""
        print(f"[ExamplePlugin] Initialized with config: {config}")
    
    @hookimpl
    def vexis_pre_execute(self, command: str, context: dict) -> str:
        """Log commands before execution"""
        self.command_count += 1
        print(f"[ExamplePlugin] Pre-execute: {command[:50]}...")
        return command
    
    @hookimpl
    def vexis_post_execute(self, command: str, result: dict, context: dict) -> None:
        """Log results after execution"""
        exit_code = result.get('exit_code', 'unknown')
        print(f"[ExamplePlugin] Post-execute: exit_code={exit_code}")
    
    @hookimpl
    def vexis_pre_phase(self, phase: str, context: dict) -> None:
        """Log phase start"""
        print(f"[ExamplePlugin] Starting phase: {phase}")
    
    @hookimpl
    def vexis_on_error(self, error: Exception, context: dict) -> bool:
        """Log errors"""
        self.error_count += 1
        print(f"[ExamplePlugin] Error occurred: {error}")
        return False  # Don't handle, just log
    
    @hookimpl
    def vexis_get_commands(self) -> list:
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
