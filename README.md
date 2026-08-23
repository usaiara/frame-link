# VirtualCamera (vcam)

iOS tweak：把摄像头的画面替换为**任意图片 / 视频 / 来自 OBS 的实时直播流**。
通过钩住 `mediaserverd`（负责连接摄像头硬件并向前端 App 转发图像数据）实现，
**无需在目标 App 里注入**，系统级生效 —— Safari / 相机 / 微信 / 视频会议等所有 App 都会显示替换后的画面。

已在 iOS 15 - 17 上测试。

> 原版：https://github.com/EthanArbuckle/vcam-ios
> 本分支增强：新增 **OBS 网络实时拉帧模式**（配合 PC 端 `obs_stream_server.py`），
> 可将 OBS 输出实时推到 iPhone，替换手机摄像头。

## 画面来源（优先级从高到低）

1. **网络模式（OBS 直播帧）**：定时从 PC 端 HTTP 服务拉取最新 JPEG 帧，实时覆盖摄像头画面。
2. **本地静态图片**（`/tmp/test.png`）：回退方案。
3. **本地视频文件**（如 `/tmp/test.mp4`）：回退方案，仅取前 60 帧循环。

## 使用步骤（OBS 直播推到 iPhone）

### 第 1 步：PC 端（Windows）启动 OBS 帧中继

1. 打开 OBS，`工具` → `WebSocket 服务器设置`，开启 WebSocket 服务器，记下端口与密码。
2. 在 OBS 里添加一个视频源（如 `显示器采集` / `窗口采集` / `视频采集设备`），并切换到该场景。
3. 启动中继脚本（需 Python3 + `websocket-client`）：

   ```bash
   pip install websocket-client
   python obs_stream_server.py --source "显示器采集" --fps 12 --port 8080
   ```

   默认连接 `ws://127.0.0.1:4455`，密码、端口、分辨率都可用命令行参数覆盖：

   ```bash
   python obs_stream_server.py --obs-url ws://127.0.0.1:4455 --password 你的密码 --port 8080 --fps 12 --width 1280 --height 720
   ```

4. 浏览器访问 `http://127.0.0.1:8080/frame.jpg` 应能看到当前 OBS 画面（会不断刷新）。
   记下电脑的局域网 IP（如 `192.168.1.100`），手机和电脑需在同一 WiFi。

### 第 2 步：手机端配置服务器地址

安装本 tweak 后，在手机 `/var/mobile/.vcam_url.txt` 写入一行：

```
http://192.168.1.100:8080/frame.jpg
```

（把 IP 换成你电脑的局域网 IP。文件保存后 1~2 秒自动生效，无需重启。）

### 第 3 步：验证

打开任意用到摄像头的 App（Safari 的 `getUserMedia` 页面、相机、微信视频），
画面应显示为 OBS 的实时输出。

> 若手机拉不到 PC 画面：先用手机浏览器访问 `http://192.168.1.100:8080/frame.jpg` 确认网络通；
> 检查 Windows 防火墙是否放行 8080 端口（允许 Python 通过专用网络）。

## 构建

需要 macOS 或 Linux + [Theos](https://theos.dev)，并准备 iOS 16.5 SDK：

```bash
./build.sh          # 会自动安装 Theos 与 SDK
# 或
make clean package FINALPACKAGE=1
```

产物在 `packages/*.deb`（rootless 布局，适用于 Dopamine / palera1n 等 rootless 越狱）。

也可以推送到 GitHub（本仓库已配置 `.github/workflows/build.yml`），
由 GitHub Actions 在 macOS 上自动构建并产出 .deb 工件。

## 安装

将 `packages/*.deb` 传到手机（scp / AirDrop / Sileo 本地安装），然后：

```bash
dpkg -i vcam_0.0.1_iphoneos-arm64.deb
killall -9 mediaserverd   # 或 重启/respring 使其生效
```

## 截图

**image file**

<img src=".imgs/image.png" width="50%">

**video file**

<img src=".imgs/video.gif" width="50%">

## 已知限制（POC）

- 内存管理较粗糙，`mediaserverd` 长时间运行可能被系统清理（每 ~30s 曾出现过泄漏）。
- 网络模式为「拉取最新帧」实现，帧率受 `obs_stream_server.py` 的 `--fps` 与手机端轮询间隔（0.15s）双重限制。
