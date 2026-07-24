# reCAPTCHA v2 Automated Bypass

A comprehensive, multi-strategy reCAPTCHA v2 bypass tool built with Python, Playwright, and AI-powered image recognition. Designed for educational purposes and authorized testing environments.

## Features

**7 Solving Strategies** — from free audio recognition to zero-trace OS-level automation:

| # | Strategy | Cost | Approach | Key Tech |
|---|----------|------|----------|----------|
| 1 | Audio Recognition | Free | Download audio challenge → speech-to-text | faster-whisper (INT8) |
| 2 | 2captcha API | ~$3/1000 | Submit sitekey to human-solving service | REST API |
| 3 | AI Image Recognition | Free | YOLOv8 classification + segmentation + CLIP | Three-engine architecture |
| 4 | Accessibility Cookie | Free | Use accessibility cookie to skip challenge | Cookie injection |
| 5 | Browser Extension | Free | NopeCHA extension auto-solve | Chrome extension |
| 6 | Stealth + Human Behavior | Free | Anti-detection fingerprint + human-like behavior | patchright + Bezier curves |
| 7 | Native Zero-Trace | Free | OS-level click + Win32 calibration + YOLO fallback | patchright + PyAutoGUI |

### Three-Engine Image Recognition Architecture

```
Challenge Grid → Engine Selection → Tile Matching → Click + Verify

┌──────────────────────────────────────────────────────┐
│  3x3 Grid (9 tiles)                                  │
│  ├── YOLOv8-cls (13-class fine-tuned, 99.88% acc)   │
│  └── CLIP fallback (non-standard categories)         │
│                                                      │
│  4x4 Grid (16 tiles)                                 │
│  ├── YOLOv8-seg (COCO segmentation + overlap ratio) │
│  └── YOLOv8-cls ranking mode (fallback)             │
│                                                      │
│  Any Grid → CLIP (zero-shot, ranked selection)       │
└──────────────────────────────────────────────────────┘
```

### Native Zero-Trace Strategy (Most Advanced)

Bypasses reCAPTCHA detection at the OS level — no CDP protocol, no `Runtime.enable` leaks:

- **patchright** `launch_persistent_context` — eliminates `webdriver` flag and `cdc_` traces
- **Win32 coordinate calibration** — `GetClientRect` + `ClientToScreen` for DPI-aware checkbox positioning
- **PyAutoGUI OS-level click** — generates `isTrusted=true` mouse events
- **Spiral search** — auto-corrects coordinate offset with expanding search pattern
- **YOLO three-engine fallback** — when image challenge triggers, solves with AI

## Project Structure

```
.
├── main.py                    # Unified entry point (GUI / CLI / direct mode)
├── gui.py                     # PyQt6 GUI with model preloading and priority queue
├── config.py                  # Configuration (reads credentials from env vars)
├── solutions.py               # Solution registry and dependency checker
├── requirements.txt           # Python dependencies
│
├── core/                      # Shared infrastructure
│   ├── base_runtime.py        # Base runtime: browser init, navigation, form submit
│   ├── model_loader.py        # Background model preloading (QThread)
│   ├── task_queue.py          # Four-level priority queue with backpressure
│   ├── persistence.py         # QSettings + SQLite (WAL mode)
│   └── window_chrome.py       # Win32 Chrome window management
│
├── runtimes/                  # Solving strategies (7 strategies)
│   ├── runtime_audio.py       # Audio recognition (faster-whisper)
│   ├── runtime_api.py         # 2captcha / CapSolver API
│   ├── runtime_image.py       # AI image recognition (YOLO + CLIP)
│   ├── runtime_cookie.py      # Accessibility cookie
│   ├── runtime_extension.py   # NopeCHA browser extension
│   ├── runtime_stealth.py     # Stealth + human behavior simulation
│   └── runtime_native.py      # Zero-trace OS-level (patchright + PyAutoGUI)
│
├── audio_solver.py            # Audio challenge solver (Whisper)
├── captcha_solver.py          # API-based solver (2captcha / CapSolver)
├── recaptcha_bypass.py        # Legacy entry point
│
├── models/                    # Pre-trained models
│   └── recaptcha_cls_best.pt  # YOLOv8-cls fine-tuned (13 reCAPTCHA classes)
│
├── extensions/nopecha/        # NopeCHA extension placeholder
└── run_e2e_test.py            # End-to-end test runner
```

## Requirements

- **Python 3.10+**
- **Windows 10/11** (Native strategy requires Win32 API + PyAutoGUI)
- **Chrome browser** (Playwright uses system Chrome via `channel="chrome"`)

### Python Dependencies

```bash
pip install -r requirements.txt
```

Key dependencies:
- `playwright` + `playwright-stealth` — Browser automation with anti-detection
- `patchright` — CDP-leak-free Playwright fork (Native strategy)
- `faster-whisper` — Speech recognition (audio strategy, INT8 quantization)
- `ultralytics` — YOLOv8 inference (image strategy)
- `transformers` + `torch` — CLIP model (image fallback)
- `PyQt6` — GUI framework
- `pyautogui` + `pywin32` — OS-level mouse control and Win32 API

After installing, run:
```bash
playwright install chromium
```

## Configuration

All sensitive configuration is read from environment variables:

```bash
# Windows
set ACCOUNT_EMAIL=your_email@gmail.com
set ACCOUNT_PASSWORD=your_password

# Optional: API keys for paid strategies
set TWOCAPTCHA_API_KEY=your_key
set CAPSOLVER_API_KEY=your_key
```

Or edit `config.py` directly with your values.

### Key Config Options

| Config | Default | Description |
|--------|---------|-------------|
| `SOLVER_METHOD` | `"audio"` | Default solving strategy |
| `BROWSER_HEADLESS` | `False` | Headless mode (may trigger more challenges) |
| `NAV_MAX_RETRIES` | `6` | Navigation retry count |
| `RECAPTCHA_MAX_RETRIES` | `6` | Solving retry count |
| `IMAGE_RANK_SCORE_GAP` | `0.45` | CLIP adaptive floor gap |
| `NATIVE_CLICK_RESULT_WAIT` | `30` | OS click response wait (seconds) |

## Usage

### GUI Mode (Default)

```bash
python main.py
```

Launches PyQt6 GUI with:
- Solution selector with dependency status
- Real-time log viewer with smart scrolling
- Model preloading progress
- Run history and success rate statistics

### CLI Mode

```bash
# List available strategies
python main.py --list

# Run specific strategy
python main.py -m native
python main.py -m audio
python main.py -m image

# Check dependencies
python main.py --check
```

### Direct Script

```bash
python recaptcha_bypass.py
```

## Technical Highlights

### Anti-Detection (Minimal Intervention Principle)

Only patches what real Chromium lacks — never overwrites real fingerprint values:

- `navigator.webdriver`: `false` → `undefined` (patchright handles this)
- `window.chrome.runtime`: added only if missing
- `cdc_` traces: cleaned from document
- Real WebGL renderer, plugins, hardwareConcurrency: **untouched** (avoiding consistency contradictions)

### Win32 Coordinate Calibration

Solves the DPI-aware checkbox positioning problem without screenshots:

```
GetClientRect (excludes window border)
  → ClientToScreen (physical screen origin)
  → + Chrome UI height (client_h - innerH × DPI)
  → + checkbox CSS × real DPI
  → Physical pixel coordinates for PyAutoGUI
```

### Page State Management

Comprehensive `page.is_closed()` checks throughout the pipeline prevent `TargetClosedError` cascades:
- Before every screenshot attempt
- Before form submission
- Before result verification
- Custom asyncio exception handler downgrades `TargetClosedError` to debug logs

### Four-Level Priority Queue

```
CRITICAL (100) → User interaction, real-time UI updates
HIGH (50)      → Real-time log updates
NORMAL (0)     → Background tasks
LOW (-50)      → Maintenance, log sampling
```

With backpressure: rejects LOW/NORMAL tasks when pending > 200, samples 90% of INFO logs.

## Testing

```bash
# Run end-to-end tests
python run_e2e_test.py

# Run specific route tests
python run_routes_test.py

# Run native strategy test
python run_native_test.py
```

## Disclaimer

This tool is developed for **educational purposes** and **authorized testing environments** only. Users are responsible for complying with applicable laws and terms of service. The authors do not condone any unauthorized or malicious use.

## License

MIT License — see [LICENSE](LICENSE) for details.

## Acknowledgments

- [ETH Zurich "Breaking reCAPTCHAv2"](https://github.com/aplesner/Breaking-reCAPTCHAv2) — YOLOv8-cls fine-tuned model source
- [patchright](https://github.com/Kaliiiiiiiiii-Vinyzu/patchright) — CDP-leak-free Playwright fork
- [playwright-stealth](https://github.com/Mattwmaster58/playwright_stealth) — Anti-detection scripts
- [faster-whisper](https://github.com/SYSTRAN/faster-whisper) — Fast speech recognition
- [ultralytics](https://github.com/ultralytics/ultralytics) — YOLOv8 framework
