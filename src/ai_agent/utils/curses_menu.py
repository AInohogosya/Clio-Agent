"""
Curses-based Arrow Key Menu System
Proper arrow key navigation without number fallbacks
Works in any terminal that supports curses
"""

import curses
import os
from typing import Optional, List, Dict, Any, Callable
from .model_definitions import MODEL_FAMILIES

# Color pairs
COLOR_TITLE = 1
COLOR_HIGHLIGHT = 2
COLOR_NORMAL = 3
COLOR_FOOTER = 4
COLOR_FILTER = 5


class CursesMenu:
    """Curses-based interactive menu with arrow key navigation and scrolling viewport"""

    def __init__(self, title: str, description: str = ""):
        self.title = title
        self.description = description
        self.items: List[Dict[str, Any]] = []
        self.current_index = 0
        self.scroll_offset = 0

    def add_item(self, display_name: str, description: str, value: Any, icon: str = "\U0001f4cb"):
        """Add an item to the menu"""
        self.items.append({
            "display_name": display_name,
            "description": description,
            "value": value,
            "icon": icon
        })

    def _calculate_viewport(self, max_y: int) -> tuple:
        """Calculate visible viewport bounds to prevent overflow"""
        header_lines = 5  # Title, separator, description, blank, instructions
        footer_lines = 2
        available_height = max_y - header_lines - footer_lines
        items_per_page = max(1, available_height // 3)  # Each item takes 3 lines
        return items_per_page, header_lines

    def run(self, stdscr) -> Optional[Any]:
        """Run the menu and return selected value with viewport scrolling"""
        # Configure curses
        curses.curs_set(0)  # Hide cursor
        stdscr.clear()

        # Initialize colors
        if curses.has_colors():
            curses.start_color()
            curses.init_pair(COLOR_TITLE, curses.COLOR_CYAN, curses.COLOR_BLACK)
            curses.init_pair(COLOR_HIGHLIGHT, curses.COLOR_BLACK, curses.COLOR_YELLOW)
            curses.init_pair(COLOR_NORMAL, curses.COLOR_WHITE, curses.COLOR_BLACK)
            curses.init_pair(COLOR_FOOTER, curses.COLOR_YELLOW, curses.COLOR_BLACK)
            curses.init_pair(COLOR_FILTER, curses.COLOR_GREEN, curses.COLOR_BLACK)

        while True:
            stdscr.clear()
            max_y, max_x = stdscr.getmaxyx()

            # Calculate viewport
            items_per_page, header_height = self._calculate_viewport(max_y)

            # Adjust scroll offset to keep current item visible
            if self.current_index < self.scroll_offset:
                self.scroll_offset = self.current_index
            elif self.current_index >= self.scroll_offset + items_per_page:
                self.scroll_offset = self.current_index - items_per_page + 1

            # Clamp scroll_offset
            self.scroll_offset = max(0, min(self.scroll_offset, len(self.items) - 1))

            # Title
            if len(self.title) < max_x:
                stdscr.addstr(0, 0, self.title, curses.A_BOLD | curses.color_pair(COLOR_TITLE) if curses.has_colors() else curses.A_BOLD)

            # Separator
            separator = "=" * min(50, max_x - 1)
            stdscr.addstr(1, 0, separator, curses.color_pair(COLOR_TITLE) if curses.has_colors() else curses.A_DIM)

            # Description
            if self.description and len(self.description) < max_x:
                stdscr.addstr(2, 0, self.description, curses.color_pair(COLOR_NORMAL) if curses.has_colors() else curses.A_NORMAL)

            # Instructions
            if max_y > 4:
                stdscr.addstr(4, 0, "Use arrows to navigate - Enter to select - Q to quit",
                             curses.color_pair(COLOR_FOOTER) if curses.has_colors() else curses.A_DIM)

            # Menu items - only draw visible items within viewport
            start_y = 6
            visible_start = self.scroll_offset
            visible_end = min(self.scroll_offset + items_per_page, len(self.items))

            # Draw scroll-up indicator
            if self.scroll_offset > 0 and start_y > 5:
                stdscr.addstr(start_y - 1, 0, "  ^ More above ^",
                             curses.color_pair(COLOR_FOOTER) if curses.has_colors() else curses.A_DIM)

            for display_idx in range(visible_end - visible_start):
                list_idx = visible_start + display_idx
                if list_idx >= len(self.items):
                    break

                item = self.items[list_idx]
                y = start_y + (display_idx * 3)

                # Don't draw below screen bounds
                if y >= max_y - 4:
                    break

                is_selected = (list_idx == self.current_index)
                line1 = "  %s %s %s" % (">" if is_selected else " ", item['icon'], item['display_name'])
                line2 = "     %s" % item['description']

                if is_selected:
                    if len(line1) < max_x:
                        stdscr.addstr(y, 0, line1, curses.color_pair(COLOR_HIGHLIGHT) | curses.A_BOLD if curses.has_colors() else curses.A_REVERSE)
                    if len(line2) < max_x:
                        stdscr.addstr(y + 1, 0, line2, curses.color_pair(COLOR_HIGHLIGHT) if curses.has_colors() else curses.A_REVERSE)
                else:
                    if len(line1) < max_x:
                        stdscr.addstr(y, 0, line1, curses.color_pair(COLOR_NORMAL) if curses.has_colors() else curses.A_NORMAL)
                    if len(line2) < max_x:
                        stdscr.addstr(y + 1, 0, line2, curses.color_pair(COLOR_NORMAL) if curses.has_colors() else curses.A_DIM)

            # Draw scroll-down indicator
            if visible_end < len(self.items):
                scroll_indicator_y = start_y + ((visible_end - visible_start) * 3)
                if scroll_indicator_y < max_y - 3:
                    stdscr.addstr(scroll_indicator_y, 0, "  v More below v",
                                 curses.color_pair(COLOR_FOOTER) if curses.has_colors() else curses.A_DIM)

            # Footer - clamp to screen bounds
            footer_y = max_y - 2
            if footer_y > start_y and footer_y < max_y:
                stdscr.addstr(footer_y, 0, separator, curses.color_pair(COLOR_TITLE) if curses.has_colors() else curses.A_DIM)

            stdscr.refresh()

            # Get key
            key = stdscr.getch()

            if key == curses.KEY_UP:
                if self.current_index > 0:
                    self.current_index -= 1
            elif key == curses.KEY_DOWN:
                if self.current_index < len(self.items) - 1:
                    self.current_index += 1
            elif key == curses.KEY_PPAGE:  # Page Up
                self.current_index = max(0, self.current_index - items_per_page)
            elif key == curses.KEY_NPAGE:  # Page Down
                self.current_index = min(len(self.items) - 1, self.current_index + items_per_page)
            elif key == curses.KEY_HOME:
                self.current_index = 0
            elif key == curses.KEY_END:
                self.current_index = max(0, len(self.items) - 1)
            elif key in [10, 13]:  # Enter key
                return self.items[self.current_index]["value"]
            elif key in [ord('q'), ord('Q'), 27]:  # Q or ESC
                return None

        return None

    def show(self) -> Optional[Any]:
        """Show the menu (entry point)"""
        return curses.wrapper(self.run)


class CursesHierarchicalMenu:
    """Curses-based hierarchical menu for model selection - single session with scrolling"""

    def __init__(self):
        # Use unified model definitions
        self.model_families = MODEL_FAMILIES

        # Build subfamilies and models from unified data
        self.subfamilies = {}
        self.models = {}

        for family_key, family_data in MODEL_FAMILIES.items():
            self.subfamilies[family_key] = {}
            for subfamily_key, subfamily_data in family_data["subfamilies"].items():
                self.subfamilies[family_key][subfamily_key] = {
                    "name": subfamily_data["name"],
                    "icon": subfamily_data.get("icon", "\U0001f4c2"),
                    "description": subfamily_data["description"]
                }
                self.models[subfamily_key] = {}
                for model_key, model_data in subfamily_data["models"].items():
                    self.models[subfamily_key][model_key] = {
                        "name": model_data.get("name", model_key),
                        "desc": model_data.get("desc", model_key),
                        "icon": model_data.get("icon", "\U0001f9e0")
                    }

    def run(self, stdscr) -> Optional[str]:
        """Run hierarchical selection in single curses session with scrolling"""
        curses.curs_set(0)

        if curses.has_colors():
            curses.start_color()
            curses.init_pair(COLOR_TITLE, curses.COLOR_CYAN, curses.COLOR_BLACK)
            curses.init_pair(COLOR_HIGHLIGHT, curses.COLOR_BLACK, curses.COLOR_YELLOW)
            curses.init_pair(COLOR_NORMAL, curses.COLOR_WHITE, curses.COLOR_BLACK)
            curses.init_pair(COLOR_FOOTER, curses.COLOR_YELLOW, curses.COLOR_BLACK)
            curses.init_pair(COLOR_FILTER, curses.COLOR_GREEN, curses.COLOR_BLACK)

        # Navigation state
        current_level = "family"
        family_key = None
        subfamily_key = None

        while True:
            if current_level == "family":
                result = self._show_family_selection(stdscr)
                if result is None:
                    return None
                family_key = result
                current_level = "subfamily"

            elif current_level == "subfamily":
                result = self._show_subfamily_selection(stdscr, family_key)
                if result is None:
                    return None
                elif result == "back":
                    current_level = "family"
                    continue
                subfamily_key = result
                current_level = "model"

            elif current_level == "model":
                result = self._show_model_selection(stdscr, family_key, subfamily_key)
                if result is None:
                    return None
                elif result == "back":
                    current_level = "subfamily"
                    continue
                elif subfamily_key == "custom" and result == "custom-input":
                    custom_model = self._get_custom_model_input(stdscr)
                    if custom_model:
                        return custom_model
                    else:
                        continue
                return result

        return None

    def _get_custom_model_input(self, stdscr) -> Optional[str]:
        """Get custom model name input from user"""
        curses.curs_set(1)  # Show cursor
        input_text = ""
        error_message = ""

        while True:
            stdscr.clear()
            max_y, max_x = stdscr.getmaxyx()

            stdscr.addstr(0, 0, "Custom Model Input", curses.A_BOLD | curses.color_pair(COLOR_TITLE))
            stdscr.addstr(1, 0, "=" * min(50, max_x - 1), curses.color_pair(COLOR_TITLE))
            stdscr.addstr(2, 0, "Enter the exact Ollama model name:", curses.color_pair(COLOR_NORMAL))
            stdscr.addstr(3, 0, "Type - Enter to confirm - Esc to cancel", curses.color_pair(COLOR_FOOTER))

            if error_message:
                stdscr.addstr(5, 0, error_message, curses.color_pair(COLOR_HIGHLIGHT))
                y_pos = 7
            else:
                y_pos = 5

            stdscr.addstr(y_pos, 0, "Model name: %s" % input_text, curses.color_pair(COLOR_NORMAL))

            if input_text:
                cursor_x = len("Model name: ") + len(input_text)
                stdscr.move(y_pos, cursor_x)
            else:
                stdscr.move(y_pos, len("Model name: "))

            stdscr.refresh()
            key = stdscr.getch()

            if key == 27:  # Escape key
                curses.curs_set(0)
                return None
            elif key in [10, 13]:  # Enter key
                if not input_text.strip():
                    error_message = "Please enter a model name."
                    continue
                else:
                    curses.curs_set(0)
                    return input_text.strip()
            elif key == curses.KEY_BACKSPACE or key == 127:
                if input_text:
                    input_text = input_text[:-1]
                    error_message = ""
            elif 32 <= key <= 126:
                if len(input_text) < 100:
                    input_text += chr(key)
                    error_message = ""

    def _calculate_viewport(self, max_y: int) -> tuple:
        """Calculate visible viewport bounds"""
        header_lines = 5  # Title, separator, description, blank, instructions
        footer_lines = 2
        available_height = max_y - header_lines - footer_lines
        items_per_page = max(1, available_height // 3)
        return items_per_page, header_lines

    def _show_list(self, stdscr, items: list, title: str, instructions: str) -> tuple:
        """Generic scrolling list display. Returns ('back', None) or (index, key)"""
        current = 0
        scroll = 0
        filter_text = ""

        def _get_filtered():
            if not filter_text:
                return list(range(len(items)))
            ft = filter_text.lower()
            result = []
            for i in range(len(items)):
                key, data = items[i]
                icon = data.get('icon', '\U0001f4cb')
                name = data.get('name', key)
                desc = data.get('description', '')
                if ft in name.lower() or ft in key.lower() or ft in desc.lower():
                    result.append(i)
            return result

        while True:
            stdscr.clear()
            max_y, max_x = stdscr.getmaxyx()

            filtered_indices = _get_filtered()

            # Reset current if out of bounds
            if current >= len(filtered_indices):
                current = max(0, len(filtered_indices) - 1)

            items_per_page, _ = self._calculate_viewport(max_y)

            # Add extra line for filter display when active
            filter_height = 1 if filter_text else 0

            # Adjust scroll
            if current < scroll:
                scroll = current
            elif current >= scroll + items_per_page:
                scroll = current - items_per_page + 1
            if filtered_indices:
                scroll = max(0, min(scroll, len(filtered_indices) - 1))
            else:
                scroll = 0

            stdscr.addstr(0, 0, title, curses.A_BOLD | curses.color_pair(COLOR_TITLE))
            stdscr.addstr(1, 0, "=" * min(50, max_x - 1), curses.color_pair(COLOR_TITLE))

            # Filter status line
            if filter_text:
                total = len(items)
                showing = len(filtered_indices)
                filter_display = "  Filter: '%s_'  %d/%d" % (filter_text, showing, total)
                stdscr.addstr(3, 0, filter_display[:max_x - 1], curses.color_pair(COLOR_FILTER) | curses.A_BOLD)
            else:
                stdscr.addstr(3, 0, "  Type to search", curses.color_pair(COLOR_FOOTER))

            stdscr.addstr(4, 0, instructions, curses.color_pair(COLOR_FOOTER))

            start_y = 6 + filter_height
            visible_start = scroll
            visible_end = min(scroll + items_per_page, len(filtered_indices))

            if scroll > 0:
                stdscr.addstr(start_y - 1, 0, "  ^ More above ^", curses.color_pair(COLOR_FOOTER))

            for display_idx in range(visible_end - visible_start):
                list_idx = visible_start + display_idx
                if list_idx >= len(filtered_indices):
                    break
                idx = filtered_indices[list_idx]
                y = start_y + (display_idx * 3)
                if y >= max_y - 4:
                    break

                key, data = items[idx]
                icon = data.get('icon', '\U0001f4cb')
                name = data.get('name', key)
                desc = data.get('description', '')

                if list_idx == current:
                    stdscr.addstr(y, 0, "  > %s %s" % (icon, name), curses.color_pair(COLOR_HIGHLIGHT) | curses.A_BOLD)
                    stdscr.addstr(y + 1, 0, "     %s" % desc, curses.color_pair(COLOR_HIGHLIGHT))
                else:
                    stdscr.addstr(y, 0, "  %s %s" % (icon, name), curses.color_pair(COLOR_NORMAL))
                    stdscr.addstr(y + 1, 0, "     %s" % desc, curses.color_pair(COLOR_NORMAL))

            if visible_end < len(filtered_indices):
                scroll_y = start_y + ((visible_end - visible_start) * 3)
                if scroll_y < max_y - 3:
                    stdscr.addstr(scroll_y, 0, "  v More below v", curses.color_pair(COLOR_FOOTER))

            stdscr.refresh()
            key = stdscr.getch()

            if key == curses.KEY_UP:
                current = max(0, current - 1)
            elif key == curses.KEY_DOWN:
                current = min(len(filtered_indices) - 1, current + 1)
            elif key == curses.KEY_PPAGE:
                current = max(0, current - items_per_page)
            elif key == curses.KEY_NPAGE:
                if filtered_indices:
                    current = min(len(filtered_indices) - 1, current + items_per_page)
                else:
                    current = 0
            elif key == curses.KEY_HOME:
                current = 0
            elif key == curses.KEY_END:
                current = max(0, len(filtered_indices) - 1)
            elif key == curses.KEY_LEFT or key == 26:
                return ("back", None)
            elif key in [10, 13]:
                if filtered_indices:
                    return ("select", items[filtered_indices[current]][0])
                return ("back", None)
            elif key in [ord('q'), ord('Q'), 27]:
                return ("quit", None)
            elif key == curses.KEY_BACKSPACE or key == 127 or key == 8:
                if filter_text:
                    filter_text = filter_text[:-1]
                    current = 0
                    scroll = 0
            elif 32 <= key <= 126:
                if len(filter_text) < 50:
                    filter_text += chr(key)
                    current = 0
                    scroll = 0

    def _show_family_selection(self, stdscr) -> Optional[str]:
        """Show family selection screen with scrolling"""
        family_items = list(self.model_families.items())
        result, value = self._show_list(stdscr, family_items,
            "Select Model Family",
            "Use arrows - Enter to select - Q to quit")
        if result == "quit":
            return None
        return value

    def _show_subfamily_selection(self, stdscr, family_key: str) -> Optional[str]:
        """Show subfamily selection screen with scrolling"""
        if family_key not in self.subfamilies:
            return None
        subfamily_items = list(self.subfamilies[family_key].items())
        family_name = self.model_families[family_key]['name']
        result, value = self._show_list(stdscr, subfamily_items,
            "%s Subfamilies" % family_name,
            "Use arrows - <- to go back - Enter to select")
        if result == "back":
            return "back"
        if result == "quit":
            return None
        return value

    def _show_model_selection(self, stdscr, family_key: str, subfamily_key: str) -> Optional[str]:
        """Show model selection screen with scrolling"""
        if subfamily_key not in self.models:
            return None
        model_items = list(self.models[subfamily_key].items())
        subfamily_name = self.subfamilies[family_key][subfamily_key]['name']
        result, value = self._show_list(
            stdscr,
            [(k, {"name": v.get("name", k), "description": v.get("desc", ""), "icon": v.get("icon", "\U0001f9e0")}) for k, v in model_items],
            "%s Models" % subfamily_name,
            "Use arrows - <- to go back - Enter to select")
        if result == "back":
            return "back"
        if result == "quit":
            return None
        return value

    def show(self) -> Optional[str]:
        """Show the hierarchical menu"""
        return curses.wrapper(self.run)


def get_curses_menu(title: str, description: str = "") -> CursesMenu:
    """Get a curses-based menu"""
    return CursesMenu(title, description)


def get_curses_hierarchical_menu() -> CursesHierarchicalMenu:
    """Get a curses-based hierarchical menu"""
    return CursesHierarchicalMenu()


def success_message(message: str):
    """Display success message"""
    print("OK: %s" % message)


def error_message(message: str):
    """Display error message"""
    print("ERROR: %s" % message)


def warning_message(message: str):
    """Display warning message"""
    print("WARN: %s" % message)


def test_curses_menu():
    """Test the curses menu"""
    menu = CursesMenu("Curses Arrow Key Menu Test", "Arrow keys only - no numbers!")

    menu.add_item("Chrome", "Google Chrome browser", "chrome", "W")
    menu.add_item("Firefox", "Mozilla Firefox", "firefox", "F")
    menu.add_item("Edge", "Microsoft Edge", "edge", "E")
    menu.add_item("Safari", "Apple Safari", "safari", "S")

    result = menu.show()

    if result:
        print("Selected: %s" % result)
    else:
        print("Cancelled")


if __name__ == "__main__":
    test_curses_menu()
