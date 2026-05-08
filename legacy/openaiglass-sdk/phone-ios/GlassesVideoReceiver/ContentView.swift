import Foundation
import SwiftUI

/// 手机 SDK运行时 主页面。
///
/// 主要功能：
/// 1. 展示当前监听状态与接收地址。
/// 2. 展示最近接收到的一帧图像和运行状态。
/// 3. 展示最近事件，便于三端联调。
struct ContentView: View {
    @Environment(\.scenePhase) private var scenePhase
    @State private var store = CameraStreamStore(capabilityRuntime: PhoneCapabilityRuntimeFactory.makeRuntime())
    @State private var server: CameraSinkServer?
    @State private var controlClient: PhoneControlClient?

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                statusSection
                previewSection
                logSection
            }
            .padding(20)
        }
        .background(Color(uiColor: .systemGroupedBackground))
        .task {
            await ensureServerStarted()
        }
        .onChange(of: scenePhase) { _, newPhase in
            guard newPhase == .active else {
                return
            }
            Task {
                await ensureServerStarted()
                controlClient?.start(with: store.preferredSinkURI)
            }
        }
    }

    /// 状态信息区域。
    ///
    /// 主要逻辑：
    /// 1. 展示监听状态、连接状态和监听地址。
    /// 2. 提供刷新本机地址按钮，便于网络变化后重新确认。
    private var statusSection: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("手机 SDK运行时")
                .font(.title2.bold())

            Label(store.statusText, systemImage: store.statusIconName)
                .font(.headline)
                .foregroundStyle(store.statusColor)

            if let lastSequence = store.latestSequence {
                Text("最近帧序号：\(lastSequence)")
                    .font(.subheadline)
            }

            if let latestReceivedAt = store.latestReceivedAtText {
                Text("最近接收时间：\(latestReceivedAt)")
                    .font(.subheadline)
            }

            VStack(alignment: .leading, spacing: 8) {
                Text("当前接收地址")
                    .font(.headline)

                if store.sinkURIs.isEmpty {
                    Text("暂未识别到可用 IPv4 地址")
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                } else {
                    ForEach(store.sinkURIs, id: \.self) { uri in
                        Text(uri)
                            .font(.footnote.monospaced())
                            .textSelection(.enabled)
                    }
                }
            }

            Text("服务端状态：\(store.controlStatusText)")
                .font(.footnote)
                .foregroundStyle(store.isServerRegistered ? .green : .secondary)

            if let retryAt = store.serverRetryAt, store.isServerRetryScheduled {
                Text("下次重试时间：\(retryAt.formatted(date: .omitted, time: .standard))")
                    .font(.footnote)
                    .foregroundStyle(.orange)
            }

            if let phoneDeviceID = store.phoneDeviceID {
                Text("手机设备编号：\(phoneDeviceID)")
                    .font(.footnote)
            }

            if let boundGlassDeviceID = store.boundGlassDeviceID {
                Text("当前绑定眼镜：\(boundGlassDeviceID)")
                    .font(.footnote)
            }

            if let activeTaskDescription = store.activeTaskDescription {
                Text("当前任务：\(activeTaskDescription)")
                    .font(.footnote)
                    .foregroundStyle(.blue)
            }

            if let latestCapabilitySummary = store.latestCapabilitySummary {
                Text("最近任务结果：\(latestCapabilitySummary)")
                    .font(.footnote)
                    .foregroundStyle((store.latestCapabilitySuccess ?? false) ? .green : .secondary)
            }

            HStack(spacing: 12) {
                Button("刷新地址") {
                    store.refreshSinkURIs()
                    controlClient?.reregisterIfNeeded(with: store.preferredSinkURI)
                }
                .buttonStyle(.bordered)

                Button("结束接收") {
                    Task {
                        await finishReceiving()
                    }
                }
                .buttonStyle(.borderedProminent)
                .tint(.blue)
            }

            if let lastError = store.lastError {
                Text("最近错误：\(lastError)")
                    .font(.footnote)
                    .foregroundStyle(.red)
            }
        }
        .padding(16)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(.background, in: RoundedRectangle(cornerRadius: 18, style: .continuous))
    }

    /// 视频预览区域。
    ///
    /// 主要逻辑：
    /// 1. 当存在最新图像时直接显示。
    /// 2. 在没有图像时显示占位提示，帮助判断当前仍未连通。
    private var previewSection: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("最近一帧图像")
                .font(.headline)

            ZStack {
                RoundedRectangle(cornerRadius: 18, style: .continuous)
                    .fill(Color.black.opacity(0.85))

                if let latestImage = store.latestImage {
                    Image(uiImage: latestImage)
                        .resizable()
                        .scaledToFit()
                        .clipShape(RoundedRectangle(cornerRadius: 18, style: .continuous))
                        .padding(8)
                    if let latestVisionOverlay = store.latestVisionOverlay {
                        DetectionOverlayView(
                            overlay: latestVisionOverlay,
                            imageSize: latestImage.size,
                            contentInset: 8
                        )
                    }
                } else {
                    VStack(spacing: 10) {
                        Image(systemName: "video.slash")
                            .font(.system(size: 36))
                        Text("等待眼镜发送图像帧")
                            .font(.headline)
                        Text("连接建立后，这里会显示最近一帧 JPEG。")
                            .font(.footnote)
                            .multilineTextAlignment(.center)
                            .foregroundStyle(.secondary)
                    }
                    .foregroundStyle(.white)
                    .padding(24)
                }
            }
            .frame(maxWidth: .infinity)
            .frame(minHeight: 260)
        }
    }

    /// 最近事件区域。
    ///
    /// 主要逻辑：
    /// 1. 只展示最近少量关键事件。
    /// 2. 便于联调时快速核对握手、接收和错误情况。
    private var logSection: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("最近事件")
                .font(.headline)

            if store.events.isEmpty {
                Text("暂无事件")
                    .font(.footnote)
                    .foregroundStyle(.secondary)
            } else {
                ForEach(store.events, id: \.self) { event in
                    Text(event)
                        .font(.footnote.monospaced())
                        .frame(maxWidth: .infinity, alignment: .leading)
                }
            }
        }
        .padding(16)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(.background, in: RoundedRectangle(cornerRadius: 18, style: .continuous))
    }

    /// 确保接收服务只启动一次。
    ///
    /// 主要逻辑：
    /// 1. 首次进入页面时创建服务对象。
    /// 2. 启动监听后刷新接收地址。
    @MainActor
    private func ensureServerStarted() async {
        guard server == nil else {
            return
        }
        let createdServer = CameraSinkServer(store: store)
        server = createdServer
        createdServer.start()
        store.refreshSinkURIs()

        let createdControlClient = PhoneControlClient(store: store)
        controlClient = createdControlClient
        createdControlClient.start(with: store.preferredSinkURI)
    }

    /// 停止当前视频接收流程。
    ///
    /// 主要逻辑：
    /// 1. 先通知服务端取消当前视频直连任务。
    /// 2. 等待眼镜侧完成停流。
    /// 3. 保持手机继续在线待命，便于后续再次开启视频。
    @MainActor
    private func finishReceiving() async {
        await controlClient?.stopVideoReceiving()
        try? await Task.sleep(for: .milliseconds(800))
        store.finishCurrentVideoSession("用户点击完成按钮")
    }
}

/// 视频预览检测框叠加层。
///
/// 主要功能：
/// 1. 按照图片在预览容器中的实际 aspect-fit 区域换算检测框坐标。
/// 2. 绘制检测框、标签和置信度，便于真机调试 YOLO 输出。
private struct DetectionOverlayView: View {
    let overlay: VisionFrameOverlay
    let imageSize: CGSize
    let contentInset: CGFloat

    var body: some View {
        GeometryReader { proxy in
            let imageRect = Self.aspectFitRect(
                imageSize: imageSize,
                containerSize: proxy.size,
                contentInset: contentInset
            )
            ZStack(alignment: .topLeading) {
                ForEach(overlay.boxes) { box in
                    let rect = Self.overlayRect(for: box, in: imageRect)
                    RoundedRectangle(cornerRadius: 4)
                        .stroke(box.isTarget ? Color.green : Color.orange, lineWidth: 3)
                        .frame(width: rect.width, height: rect.height)
                        .position(x: rect.midX, y: rect.midY)

                    Text("\(box.label) \(Self.percentText(box.confidence))")
                        .font(.caption2.bold())
                        .foregroundStyle(.white)
                        .lineLimit(1)
                        .padding(.horizontal, 6)
                        .padding(.vertical, 3)
                        .background(box.isTarget ? Color.green.opacity(0.9) : Color.orange.opacity(0.9))
                        .clipShape(RoundedRectangle(cornerRadius: 4, style: .continuous))
                        .position(
                            x: min(max(rect.minX + 42, imageRect.minX + 42), imageRect.maxX - 42),
                            y: max(rect.minY - 10, imageRect.minY + 12)
                        )
                }
            }
            .frame(width: proxy.size.width, height: proxy.size.height, alignment: .topLeading)
        }
        .allowsHitTesting(false)
    }

    /// 计算图片在预览容器中的实际显示区域。
    ///
    /// 参数：
    /// 1. `imageSize`：原始图片尺寸。
    /// 2. `containerSize`：预览容器尺寸。
    /// 3. `contentInset`：图片四周内边距。
    ///
    /// 返回值：
    /// 1. 图片 aspect-fit 后的显示矩形。
    private static func aspectFitRect(imageSize: CGSize, containerSize: CGSize, contentInset: CGFloat) -> CGRect {
        let availableWidth = max(1, containerSize.width - contentInset * 2)
        let availableHeight = max(1, containerSize.height - contentInset * 2)
        guard imageSize.width > 0, imageSize.height > 0 else {
            return CGRect(x: contentInset, y: contentInset, width: availableWidth, height: availableHeight)
        }
        let scale = min(availableWidth / imageSize.width, availableHeight / imageSize.height)
        let width = imageSize.width * scale
        let height = imageSize.height * scale
        return CGRect(
            x: (containerSize.width - width) / 2,
            y: (containerSize.height - height) / 2,
            width: width,
            height: height
        )
    }

    /// 将 Vision 归一化检测框转换为 SwiftUI 显示坐标。
    ///
    /// 参数：
    /// 1. `box`：归一化检测框，原点在左下角。
    /// 2. `imageRect`：图片实际显示区域。
    ///
    /// 返回值：
    /// 1. SwiftUI 坐标系下的检测框。
    private static func overlayRect(for box: VisionOverlayBox, in imageRect: CGRect) -> CGRect {
        let x = imageRect.minX + CGFloat(box.x) * imageRect.width
        let y = imageRect.minY + CGFloat(1 - box.y - box.height) * imageRect.height
        let width = CGFloat(box.width) * imageRect.width
        let height = CGFloat(box.height) * imageRect.height
        return CGRect(x: x, y: y, width: width, height: height)
    }

    /// 格式化置信度百分比。
    private static func percentText(_ value: Double) -> String {
        "\(Int((value * 100).rounded()))%"
    }
}
