# 🟡 Yellow Selection System

## Overview
A unified, reproducible menu system with consistent yellow highlighting for all selection interfaces in the VEXIS AI Agent.

## Features
- 🟡 **Consistent Yellow Highlighting**: All selections use the same yellow color scheme
- 🎯 **Reproducible Configuration**: Centralized settings for cross-platform consistency
- 🧹 **Clean Display**: In-place updates without creating confusing logs
- 🎮 **Multiple Navigation**: Arrow keys, number keys, and Enter support
- 📱 **Cross-Platform**: Works on Windows, Linux, and macOS

## Files

### Core Components
- **`__init__.py`**: Package initialization and exports
- **`main.py`**: Main entry point and convenience functions
- **`config.py`**: Reproducible configuration settings
- **`clean_interactive_menu.py`**: Core menu implementation
- **`clean_hierarchical_selector.py`**: Hierarchical model selection

### Configuration
The `config.py` file provides:
- **Color definitions**: Standardized ANSI codes
- **Navigation settings**: Arrow key mappings
- **Display settings**: Screen clearing and cursor control
- **Model families**: Hierarchical model organization
- **Reproducibility flags**: Consistency settings

## Usage

### Basic Menu
```python
from .yellow_selection import get_yellow_menu

menu = get_yellow_menu("Select Option", "Choose your preference:")
menu.add_item("Option 1", "Description", "value1", "📋")
selected = menu.show()
```

### Hierarchical Selection
```python
from .yellow_selection import get_yellow_selector

selector = get_yellow_selector()
selected_model = selector.interactive_model_selection()
```

### Provider Selection
```python
from .yellow_selection.main import create_provider_menu

menu = create_provider_menu()
selected_provider = menu.show()
```

## Reproducibility

The system uses centralized configuration to ensure consistent behavior:

- **Colors**: All color codes defined in one place
- **Navigation**: Arrow key mappings standardized
- **Display**: Screen handling commands unified
- **Cross-platform**: Platform-specific fallbacks included

## Integration

The system is integrated into:
- **`ollama_model_selector.py`**: Model selection interface
- **`run.py`**: Provider selection (via interactive_menu.py)

## Benefits

1. **Consistency**: Same yellow highlighting everywhere
2. **Maintainability**: Single source of truth for menu behavior
3. **Reproducibility**: Predictable behavior across platforms
4. **User Experience**: Clean, intuitive interface
5. **Performance**: Efficient in-place updates

## Version
- **Version**: 1.0.0
- **Compatibility**: Python 3.7+
- **Dependencies**: Minimal (only standard library)

## Future Enhancements
- Theme support for different color schemes
- Accessibility improvements
- Advanced navigation options
- Performance optimizations
