"""
Plugin System for VEXIS-CLI
Extensible architecture using pluggy
"""

import pluggy

# Define hook specifications namespace
hookspec = pluggy.HookspecMarker("vexis")
hookimpl = pluggy.HookimplMarker("vexis")


class VexisHooks:
    """
    Hook specifications for VEXIS-CLI plugins
    """
    
    @hookspec
    def vexis_initialize(self, config: dict) -> None:
        """
        Called when VEXIS initializes
        
        Args:
            config: Application configuration dictionary
        """
        pass
    
    @hookspec
    def vexis_pre_execute(self, command: str, context: dict) -> str:
        """
        Called before executing a terminal command
        
        Args:
            command: The command to be executed
            context: Execution context (phase, iteration, etc.)
            
        Returns:
            Modified command or original command
        """
        pass
    
    @hookspec
    def vexis_post_execute(self, command: str, result: dict, context: dict) -> None:
        """
        Called after executing a terminal command
        
        Args:
            command: The executed command
            result: Execution result (stdout, stderr, exit_code)
            context: Execution context
        """
        pass
    
    @hookspec
    def vexis_pre_phase(self, phase: str, context: dict) -> None:
        """
        Called before starting a pipeline phase
        
        Args:
            phase: Phase name (phase1, phase2, etc.)
            context: Pipeline context
        """
        pass
    
    @hookspec
    def vexis_post_phase(self, phase: str, result: dict, context: dict) -> None:
        """
        Called after completing a pipeline phase
        
        Args:
            phase: Phase name
            result: Phase execution result
            context: Pipeline context
        """
        pass
    
    @hookspec
    def vexis_pre_request(self, request: dict, provider: str, model: str) -> dict:
        """
        Called before making an API request
        
        Args:
            request: API request data
            provider: Provider name
            model: Model name
            
        Returns:
            Modified request data
        """
        pass
    
    @hookspec
    def vexis_post_response(self, response: dict, provider: str, model: str) -> dict:
        """
        Called after receiving an API response
        
        Args:
            response: API response data
            provider: Provider name
            model: Model name
            
        Returns:
            Modified response data
        """
        pass
    
    @hookspec
    def vexis_on_error(self, error: Exception, context: dict) -> bool:
        """
        Called when an error occurs
        
        Args:
            error: The exception that occurred
            context: Error context (phase, provider, etc.)
            
        Returns:
            True if error was handled, False to propagate
        """
        pass
    
    @hookspec
    def vexis_get_commands(self) -> list:
        """
        Return custom CLI commands to register
        
        Returns:
            List of (command_name, handler_function, description) tuples
        """
        pass
    
    @hookspec
    def vexis_get_providers(self) -> list:
        """
        Return custom AI providers to register
        
        Returns:
            List of provider classes implementing BaseLLM interface
        """
        pass


class PluginManager:
    """
    Manages plugin lifecycle and hook execution
    """
    
    def __init__(self):
        self.pm = pluggy.PluginManager("vexis")
        self.pm.add_hookspecs(VexisHooks)
        self._plugins = []
    
    def register_plugin(self, plugin):
        """Register a plugin module or class"""
        self.pm.register(plugin)
        self._plugins.append(plugin)
    
    def unregister_plugin(self, plugin):
        """Unregister a plugin"""
        self.pm.unregister(plugin)
        self._plugins.remove(plugin)
    
    def discover_plugins(self, entry_points_group: str = "vexis.plugins"):
        """Discover and load plugins from entry points"""
        self.pm.load_setuptools_entrypoints(entry_points_group)
    
    def get_hook_caller(self):
        """Get the hook caller for invoking hooks"""
        return self.pm.hook
    
    def list_plugins(self) -> list:
        """List registered plugins"""
        return self._plugins.copy()


# Global plugin manager instance
_global_plugin_manager: PluginManager = None


def get_plugin_manager() -> PluginManager:
    """Get or create global plugin manager"""
    global _global_plugin_manager
    if _global_plugin_manager is None:
        _global_plugin_manager = PluginManager()
    return _global_plugin_manager


def initialize_plugins(config: dict = None):
    """Initialize and discover plugins"""
    pm = get_plugin_manager()
    pm.discover_plugins()
    
    # Call initialization hook
    hook = pm.get_hook_caller()
    hook.vexis_initialize(config=config or {})
    
    return pm
