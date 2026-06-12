"""
Clean Interactive Menu - No Log Creation
Updates existing display without creating new content below
"""

import sys
import os
from typing import Optional, List, Any
try:
    from .config import get_colors, get_navigation_config, get_display_config
except ImportError:
    # Fallback for direct execution - use the local config
    try:
        from config import get_colors, get_navigation_config, get_display_config
    except ImportError:
        # Ultimate fallback - define colors inline
        def get_colors():
            return {
                'RESET': '\033[0m',
                'BOLD': '\033[1m',
                'BLACK': '\033[30m',
                'YELLOW': '\033[33m',
                'BRIGHT_YELLOW': '\033[93m',
                'BG_YELLOW': '\033[43m',
                'WHITE': '\033[97m',
                'BRIGHT_WHITE': '\033[37m',
                'BRIGHT_GREEN': '\033[92m',
                'RED': '\033[91m',
                'CYAN': '\033[36m',
                'BRIGHT_CYAN': '\033[96m'
            }
        def get_navigation_config():
            return {'navigation': {}, 'arrow_keys': {}}
        def get_display_config():
            return {'display': {}}


class Colors:
    """Color constants from reproducible configuration"""
    def __init__(self):
        colors = get_colors()
        for key, value in colors.items():
            setattr(self, key, value)
    
    RESET = get_colors()['RESET']
    BOLD = get_colors()['BOLD']
    CYAN = get_colors()['CYAN']
    BRIGHT_CYAN = get_colors()['BRIGHT_CYAN']
    BRIGHT_WHITE = get_colors()['BRIGHT_WHITE']
    BRIGHT_YELLOW = get_colors()['BRIGHT_YELLOW']
    BRIGHT_GREEN = get_colors()['BRIGHT_GREEN']
    RED = get_colors()['RED']
    YELLOW = get_colors()['YELLOW']
    BG_YELLOW = get_colors()['BG_YELLOW']
    WHITE = get_colors()['WHITE']
    BLACK = get_colors()['BLACK']


# Initialize colors from config
_colors = Colors()
RESET = _colors.RESET
BOLD = _colors.BOLD
CYAN = _colors.CYAN
BRIGHT_CYAN = _colors.BRIGHT_CYAN
BRIGHT_WHITE = _colors.BRIGHT_WHITE
BRIGHT_YELLOW = _colors.BRIGHT_YELLOW
BRIGHT_GREEN = _colors.BRIGHT_GREEN
RED = _colors.RED
YELLOW = _colors.YELLOW
BG_YELLOW = _colors.BG_YELLOW
WHITE = _colors.WHITE
BLACK = _colors.BLACK


class CleanInteractiveMenu:
    """Clean menu that updates display without creating new content"""
    
    def __init__(self, title: str, description: str):
        self.title = title
        self.description = description
        self.items = []
        self.current_index = 0
        self.displayed_once = False
        self.filter_text = ""
        
    def add_item(self, display_name: str, description: str, value: Any, icon: str = "📋"):
        self.items.append({
            "display_name": display_name,
            "description": description,
            "value": value,
            "icon": icon
        })

    def _get_filtered_indices(self):
        if not self.filter_text:
            return list(range(len(self.items)))
        ft = self.filter_text.lower()
        result = []
        for i, item in enumerate(self.items):
            name = item.get("display_name", "")
            desc = item.get("description", "")
            val = str(item.get("value", ""))
            if ft in name.lower() or ft in desc.lower() or ft in val.lower():
                result.append(i)
        return result
    
    def clear_screen(self):
        """Clear screen only once"""
        if not self.displayed_once:
            print("\033[2J\033[H", end="", flush=True)
            self.displayed_once = True
    
    def display_header(self):
        """Display header only once"""
        if not self.displayed_once:
            print(f"{Colors.BOLD}{Colors.BRIGHT_CYAN}🔧 {self.title}{Colors.RESET}")
            print(f"{Colors.BRIGHT_CYAN}{'─' * 50}{Colors.RESET}")
            print(f"{Colors.CYAN}📝 {self.description}{Colors.RESET}")
            print()
            print(f"{Colors.BOLD}📋 Available Options:{Colors.RESET}")
            print()
            print(f"{Colors.BRIGHT_YELLOW}💡 Use ↑↓ arrows • 1-9 numbers • Type to search • Enter to select • 'q' to quit{Colors.RESET}")
            print()
            print()
    
    def display_footer(self):
        """Display footer only once"""
        if not self.displayed_once:
            print(f"{Colors.BRIGHT_CYAN}{'─' * 50}{Colors.RESET}")
            # Add extra lines to prevent footer overlap
            print()
            print()
    
    def update_display(self):
        """Update only the menu items, no new content"""
        filtered = self._get_filtered_indices()
        
        # Calculate starting line for menu items (after header + filter line)
        header_lines = 8  # Title, separator, description, empty, "Available Options", empty, footer, empty
        
        # Show filter status
        filter_line = header_lines
        print(f"\033[{filter_line};0H", end="", flush=True)
        print(f"\033[K", end="", flush=True)
        if self.filter_text:
            print(f"{Colors.BRIGHT_GREEN}  🔍 Filter: '{self.filter_text}_'  ({len(filtered)}/{len(self.items)} items){Colors.RESET}", end="", flush=True)
        else:
            print(f"  Type to search...", end="", flush=True)
        
        item_start = filter_line + 1
        for display_idx, idx in enumerate(filtered):
            item = self.items[idx]
            item_line = item_start + (display_idx * 3)
            desc_line = item_line + 1
            
            # Update item line
            print(f"\033[{item_line};0H", end="", flush=True)
            print(f"\033[K", end="", flush=True)
            
            if display_idx == self.current_index:
                print(f"{Colors.BG_YELLOW}{Colors.BLACK}  ▶ [{idx+1}] {item['icon']} {item['display_name']}{Colors.RESET}", end="", flush=True)
            else:
                print(f"  [{idx+1}] {item['icon']} {item['display_name']}", end="", flush=True)
            
            # Update description line
            print(f"\033[{desc_line};0H", end="", flush=True)
            print(f"\033[K", end="", flush=True)
            
            if display_idx == self.current_index:
                print(f"{Colors.BG_YELLOW}{Colors.BLACK}       {item['description']}{Colors.RESET}", end="", flush=True)
            else:
                print(f"       {item['description']}", end="", flush=True)
    
    def get_key(self) -> str:
        """Get key press with universal arrow key detection"""
        try:
            # Universal detection that works in all terminals
            return self._universal_get_key()
        except Exception as e:
            # Fallback to simple input
            return self._fallback_input()
    
    def _universal_get_key(self) -> str:
        """Universal key detection that works anywhere"""
        try:
            import select
            
            # Use select with timeout - most reliable method
            if select.select([sys.stdin], [], [], 0.1) == ([sys.stdin], [], []):
                ch = sys.stdin.read(1)
                
                if ch == '\x1b':
                    # Try to read the full escape sequence
                    seq = ch
                    for _ in range(3):  # Try to read up to 3 more chars
                        if select.select([sys.stdin], [], [], 0.01) == ([sys.stdin], [], []):
                            seq += sys.stdin.read(1)
                        else:
                            break
                    
                    # Normalize all possible arrow sequences
                    if seq in ['\x1b[A', '\x1bOA', '\x1b[A\x1bOA']:  # Up variations
                        return '\x1b[A'
                    elif seq in ['\x1b[B', '\x1bOB', '\x1b[B\x1bOB']:  # Down variations
                        return '\x1b[B'
                    elif seq in ['\x1b[C', '\x1bOC', '\x1b[C\x1bOC']:  # Right variations
                        return '\x1b[C'
                    elif seq in ['\x1b[D', '\x1bOD', '\x1b[D\x1bOD']:  # Left variations
                        return '\x1b[D'
                    else:
                        return seq  # Return other sequences as-is
                
                elif ch in ['\r', '\n']:
                    return '\r'
                elif ch.lower() in ['q', 'Q']:
                    return 'q'
                elif ch.isdigit():
                    return ch
                elif ch and len(ch) == 1:
                    return ch
            
            return ''
            
        except Exception as e:
            return ''
    
    def _fallback_input(self) -> str:
        """Fallback to regular input() method"""
        try:
            choice = input(f"{BRIGHT_YELLOW}Enter choice (1-{len(self.items)}) or 'q': {RESET}").strip()
            if choice.lower() == 'q':
                return 'q'
            elif choice.isdigit() and 1 <= int(choice) <= len(self.items):
                return choice
            else:
                return ''  # Invalid input
        except (EOFError, KeyboardInterrupt):
            return ''
        except Exception as e:
            return ''
    
    def show(self) -> Optional[Any]:
        """Show clean interactive menu with improved display handling and search filter"""
        try:
            # Clear screen once at start
            print("\033[2J\033[H", end="", flush=True)
            self.displayed_once = True
            self.display_header()
            
            filtered = self._get_filtered_indices()
            
            # Initial display of filtered items
            for display_idx, idx in enumerate(filtered):
                item = self.items[idx]
                if display_idx == self.current_index:
                    print(f"{Colors.BG_YELLOW}{Colors.BLACK}  ▶ [{idx+1}] {item['icon']} {item['display_name']}{Colors.RESET}")
                    print(f"{Colors.BG_YELLOW}{Colors.BLACK}       {item['description']}{Colors.RESET}")
                else:
                    print(f"  [{idx+1}] {item['icon']} {item['display_name']}")
                    print(f"       {item['description']}")
                print()
            
            self.display_footer()
            
            while True:
                key = self.get_key()
                
                # Skip empty keys (no input)
                if not key or key == '':
                    continue
                
                # Handle input with improved key detection
                if isinstance(key, str) and key.lower() == 'q':
                    # Clear screen before exit
                    print("\033[2J\033[H", end="", flush=True)
                    return None
                elif key in ['\r', '\n']:  # Enter key
                    filtered = self._get_filtered_indices()
                    if filtered:
                        selected_item = self.items[filtered[self.current_index]]
                        # Clear screen before return
                        print("\033[2J\033[H", end="", flush=True)
                        return selected_item["value"]
                    return None
                elif key == '\x1b[A':  # Up arrow
                    old_index = self.current_index
                    filtered = self._get_filtered_indices()
                    self.current_index = max(0, self.current_index - 1)
                    if old_index != self.current_index:
                        self.update_display()
                elif key == '\x1b[B':  # Down arrow
                    old_index = self.current_index
                    filtered = self._get_filtered_indices()
                    self.current_index = min(len(filtered) - 1, self.current_index + 1)
                    if old_index != self.current_index:
                        self.update_display()
                elif isinstance(key, str) and key.isdigit():  # Number input
                    try:
                        num = int(key)
                        filtered = self._get_filtered_indices()
                        if 1 <= num <= len(self.items):
                            old_index = self.current_index
                            self.current_index = num - 1
                            if old_index != self.current_index:
                                self.update_display()
                    except (ValueError, IndexError):
                        pass
                elif isinstance(key, str) and len(key) == 1 and key.isprintable() and not key.isdigit():
                    # Printable character: add to filter
                    self.filter_text += key
                    self.current_index = 0
                    self.update_display()
                elif key == '\x7f' or key == '\b':  # Backspace
                    if self.filter_text:
                        self.filter_text = self.filter_text[:-1]
                        self.current_index = 0
                        self.update_display()
                    
        except KeyboardInterrupt:
            print("\033[2J\033[H", end="", flush=True)
            return None
        except Exception as e:
            return self.fallback_selection()
    
    def fallback_selection(self) -> Optional[Any]:
        """Fallback to numbered selection with clean display and search filter"""
        print(f"\n{Colors.RED}⚠️  Using simple selection as fallback{Colors.RESET}")
        print()
        
        filter_text = self.filter_text
        filtered = self._get_filtered_indices()
        
        if filter_text:
            print(f"{Colors.BRIGHT_GREEN}  🔍 Filter: '{filter_text}' ({len(filtered)}/{len(self.items)} items){Colors.RESET}")
            print()
        
        for display_idx, idx in enumerate(filtered, 1):
            item = self.items[idx]
            print(f"{display_idx}. {item['icon']} {item['display_name']}")
            print(f"   {item['description']}")
            print()
        
        while True:
            try:
                prompt = f"{Colors.YELLOW}Select (1-{len(filtered)}) or 'q': {Colors.RESET}"
                choice = input(prompt).strip()
                if choice.lower() == 'q':
                    print("\033[2J\033[H", end="", flush=True)
                    return None
                choice_idx = int(choice) - 1
                if 0 <= choice_idx < len(filtered):
                    print("\033[2J\033[H", end="", flush=True)
                    return self.items[filtered[choice_idx]]["value"]
            except (ValueError, KeyboardInterrupt):
                print("\033[2J\033[H", end="", flush=True)
                return None


def success_message(message: str):
    print(f"{Colors.BRIGHT_GREEN}✓ {message}{Colors.RESET}")


def error_message(message: str):
    print(f"{Colors.RED}✗ {message}{Colors.RESET}")


def warning_message(message: str):
    print(f"{Colors.BRIGHT_YELLOW}⚠ {message}{Colors.RESET}")
