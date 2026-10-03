/**
 * Minimal terminal emulator used to assert on what a full-screen TUI actually
 * paints. Ink only rewrites the cells that changed, so scraping the raw byte
 * stream shows stale text; replaying it into a screen buffer does not.
 */
const CSI = '\u001B[';

function parseParams(body) {
  return body.split(';').map((part) => (part === '' ? 0 : Number.parseInt(part, 10)));
}

export function replay(input, { columns = 80, rows = 24 } = {}) {
  const grid = Array.from({ length: rows }, () => Array.from({ length: columns }, () => ' '));
  let row = 0;
  let column = 0;
  let index = 0;

  const put = (text) => {
    for (const character of text) {
      if (character === '\n') {
        row = Math.min(rows - 1, row + 1);
        continue;
      }
      if (character === '\r') {
        column = 0;
        continue;
      }
      if (row < 0 || row >= rows || column < 0 || column >= columns) continue;
      grid[row][column] = character;
      column += 1;
    }
  };

  const eraseLine = (mode) => {
    if (row < 0 || row >= rows) return;
    if (mode === 0) for (let c = column; c < columns; c += 1) grid[row][c] = ' ';
    else if (mode === 1) for (let c = 0; c <= column && c < columns; c += 1) grid[row][c] = ' ';
    else grid[row] = Array.from({ length: columns }, () => ' ');
  };

  const eraseDisplay = (mode) => {
    if (mode === 2 || mode === 3) {
      for (let r = 0; r < rows; r += 1) grid[r] = Array.from({ length: columns }, () => ' ');
      return;
    }
    if (mode === 0) {
      eraseLine(0);
      for (let r = row + 1; r < rows; r += 1) grid[r] = Array.from({ length: columns }, () => ' ');
    }
  };

  while (index < input.length) {
    const character = input[index];

    if (character === '\u001B') {
      const next = input[index + 1];
      if (next === '[') {
        let cursor = index + 2;
        while (cursor < input.length && !/[A-Za-z]/.test(input[cursor])) cursor += 1;
        const body = input.slice(index + 2, cursor);
        const final = input[cursor] ?? '';
        const params = parseParams(body);
        const first = params[0] ?? 0;
        switch (final) {
          case 'A': row = Math.max(0, row - Math.max(1, first)); break;
          case 'B': row = Math.min(rows - 1, row + Math.max(1, first)); break;
          case 'C': column = Math.min(columns - 1, column + Math.max(1, first)); break;
          case 'D': column = Math.max(0, column - Math.max(1, first)); break;
          case 'E': row = Math.min(rows - 1, row + Math.max(1, first)); column = 0; break;
          case 'F': row = Math.max(0, row - Math.max(1, first)); column = 0; break;
          case 'G': column = Math.max(0, Math.min(columns - 1, (first || 1) - 1)); break;
          case 'H':
          case 'f': {
            const targetRow = (params[0] || 1) - 1;
            const targetColumn = (params[1] || 1) - 1;
            row = Math.max(0, Math.min(rows - 1, targetRow));
            column = Math.max(0, Math.min(columns - 1, targetColumn));
            break;
          }
          case 'J': eraseDisplay(first); break;
          case 'K': eraseLine(first); break;
          case 'X': {
            const count = Math.max(1, params[0] || 1);
            for (let c = column; c < Math.min(columns, column + count); c += 1) grid[row][c] = ' ';
            break;
          }
          case 'P': {
            const count = Math.max(1, params[0] || 1);
            grid[row].splice(column, count);
            while (grid[row].length < columns) grid[row].push(' ');
            break;
          }
          case '@': {
            const count = Math.max(1, params[0] || 1);
            for (let c = 0; c < count; c += 1) grid[row].splice(column, 0, ' ');
            grid[row].length = columns;
            break;
          }
          default: break;
        }
        index = cursor + 1;
        continue;
      }
      if (next === ']') {
        const end = input.indexOf('\u0007', index);
        index = end === -1 ? input.length : end + 1;
        continue;
      }
      if (next === '(' || next === ')') {
        index += 3;
        continue;
      }
      index += 2;
      continue;
    }

    put(character);
    index += 1;
  }

  return grid.map((line) => line.join('').replace(/\s+$/, ''));
}

export function screenText(input, options) {
  return replay(input, options).join('\n');
}

export { CSI };
