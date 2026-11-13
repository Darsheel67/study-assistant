# Study Assistant

A background exam and quiz study assistant for Windows that reads questions from your screen and solves them using fast AI models (Groq and Google Gemini).

## Features

- **Stealth Background Daemon**: Runs completely silently with no main window distracting your desktop.
- **Micro Floating Pill**: Displays only the final answer in a tiny pill at the bottom-right corner of your screen. 1-click dismisses it.
- **Line Accumulator (`Alt + E`)**: Hover over individual lines or code blocks to capture them sequentially, then press `Alt + Q` to merge and solve.
- **Fast Multimodal Vision**: Uses Groq Vision and Gemini Vision to read code, formulas, and symbols directly from screen pixels.
- **Multi-Model Consensus**: Cross-checks answers in parallel between Groq and Gemini for high accuracy.
- **Local Python Sandbox**: Safely executes Python code snippets offline to verify program output questions in milliseconds.

---

## Installation

### Prerequisites
- Windows 10 or 11
- Python 3.10 or newer (make sure to check **"Add Python to PATH"** during install)

### Setup

1. Clone or download this repository:
   ```cmd
   git clone https://github.com/your-username/study-assistant.git
   cd study-assistant
   ```

2. Run the 1-click setup script:
   ```cmd
   setup.bat
   ```
   *(Or manually install dependencies)*:
   ```cmd
   python -m pip install -r requirements.txt
   ```

3. Create your `.env` configuration:
   - Copy `.env.example` to `.env`.
   - Open `.env` in any text editor and add your free API keys:
     ```env
     GROQ_API_KEY=gsk_your_groq_key_here
     GEMINI_API_KEY=AIzaSy_your_gemini_key_here
     ```

---

## Getting Free API Keys

- **Groq API Key**: Visit [console.groq.com/keys](https://console.groq.com/keys) (Sign in -> Create API Key).
- **Google Gemini API Key**: Visit [aistudio.google.com/apikey](https://aistudio.google.com/apikey) (Sign in -> Create API Key).

Both provide generous free tiers suitable for daily study use.

---

## Usage

Double-click **`run_assistant.bat`** (or run `python study_assistant.py`).

### Keyboard Shortcuts

| Shortcut | Description |
| :--- | :--- |
| `Alt + E` | **Capture Line**: Hover mouse over any line/code line to append it to the buffer. |
| `Alt + Q` | **Solve**: Merges accumulated lines and solves, or auto-solves the question under your cursor. |
| `Alt + C` | **Clear Buffer**: Empties accumulated lines from memory. |
| `Alt + S` | **Quick Snip**: Drag a box over any tricky question or diagram. |
| `Alt + W` / `Esc` | **Dismiss**: Instantly hides the answer pill (or click anywhere on the pill). |

---

## License

This project is licensed under the MIT License - see the LICENSE file for details.
