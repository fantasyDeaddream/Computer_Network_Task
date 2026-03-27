# PyAudio 安装指南

## 问题说明

PyAudio 需要编译 C 扩展，在 Windows 上需要 Microsoft Visual C++ 14.0 或更高版本。

## 解决方案

### 方案 1：安装预编译的 wheel 文件（推荐）

从非官方源下载预编译的 wheel 文件：

```bash
# 访问以下网站下载对应 Python 版本的 wheel 文件
# https://www.lfd.uci.edu/~gohlke/pythonlibs/#pyaudio

# 例如，对于 Python 3.14，下载：
# PyAudio‑0.2.14‑cp314‑cp314‑win_amd64.whl

# 然后安装：
pip install PyAudio‑0.2.14‑cp314‑cp314‑win_amd64.whl
```

### 方案 2：安装 Microsoft C++ Build Tools

1. 访问 https://visualstudio.microsoft.com/visual-cpp-build-tools/
2. 下载并安装 "Build Tools for Visual Studio"
3. 在安装程序中选择 "C++ build tools" 工作负载
4. 安装完成后，运行：

```bash
pip install pyaudio
```

### 方案 3：使用 pipwin（Windows）

```bash
pip install pipwin
pipwin install pyaudio
```

### 方案 4：使用 conda（如果使用 Anaconda）

```bash
conda install -c anaconda pyaudio
```

## 验证安装

安装完成后，运行以下命令验证：

```bash
python -c "import pyaudio; print('PyAudio version:', pyaudio.__version__)"
```

## 当前状态

- 测试框架（pytest, hypothesis）已安装并正常工作
- 项目结构已搭建完成
- PyAudio 暂未安装，但不影响测试框架的运行
- 实际使用音频功能时需要安装 PyAudio

## 临时解决方案

在 `audio_config.py` 中，我们使用了 try-except 块来处理 PyAudio 未安装的情况：

```python
try:
    import pyaudio
    AUDIO_FORMAT = pyaudio.paInt16
except ImportError:
    AUDIO_FORMAT = 8  # paInt16的值
```

这样可以让测试和开发继续进行，实际使用音频功能时再安装 PyAudio。
