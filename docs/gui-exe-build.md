# GUI 打包成带图标的 EXE

这个仓库里的 `gui.py` 已经支持读取 `assets/app_icon.svg` 作为窗口图标，并提供了一个一键打包脚本：

- `scripts/generate_windows_icon.py`：本地生成 Windows 用的 `build/app_icon.ico`（不提交到 Git）
- `scripts/build_gui_exe.py`：调用 PyInstaller 打包 GUI，并强制使用 PySide6、排除 PyQt6 / PyQt5 / PySide2 冲突
- `build_gui_exe.bat`：Windows 下直接双击运行

## 依赖安装

建议先在 Windows 虚拟环境里执行：

```bash
python -m pip install -e .
python -m pip install PySide6 "pyqtdarktheme>=2.1.0" python-dotenv pyinstaller
```

## 命令行打包

```bash
python scripts/build_gui_exe.py
```

默认输出：

- EXE：`dist/DeepgramSubtitleGUI/DeepgramSubtitleGUI.exe`
- 构建缓存：`build/`
- 图标：本地生成的 `build/app_icon.ico`

## 双击打包

直接运行：

```bat
build_gui_exe.bat
```

## 自定义图标

如果你想换成自己的图标，可以：

1. 替换 `assets/app_icon.svg`，让 GUI 窗口使用新图标。
2. 重新运行 `python scripts/generate_windows_icon.py` 生成新的本地 `build/app_icon.ico`。
3. 再执行 `python scripts/build_gui_exe.py` 重新打包。

## 注意事项

- EXE 打包最好在 Windows 上执行，这样生成的就是原生 `.exe`。
- 构建脚本会设置 `QT_API=pyside6`，并通过 `--exclude-module` 排除 `PyQt6`、`PyQt5`、`PySide2`，避免 PyInstaller 因多个 Qt 绑定同时存在而报错。
- `build/app_icon.ico` 是构建时临时生成的，本仓库不跟踪该二进制文件，避免 PR / Web 提交时报“不支持二进制文件”。
- `--add-data` 会把 `assets/` 一起带进安装目录，保证运行时仍能拿到 SVG 图标等运行时资源。
- 如果系统没有安装 `pyinstaller`，脚本会直接提示安装命令。
