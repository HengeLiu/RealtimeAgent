import Foundation

#if os(iOS)
import CoreLocation

/// 定位结果的 Sendable 包装。
///
/// 主要功能：让主线程产出的 `[String: Any]` payload 能安全跨越 actor 边界回到客户端命令处理层。
struct RealtimeAgentLocationPayload: @unchecked Sendable {
    let fields: [String: Any]
}

/// 端侧定位提供者。
///
/// 主要功能：用 CoreLocation 获取一次当前位置，对齐 JavaScript SDK 的 `device.location.get_current` 行为。
/// 主要逻辑：先确保 when-in-use 授权，再 `requestLocation()` 取单次定位，并附带超时保护。
/// 异常情况：授权被拒、定位失败或超时时抛出 `LocationError`，由命令处理层转成 `command.failed`。
@MainActor
final class RealtimeAgentLocationProvider: NSObject, CLLocationManagerDelegate {
    /// 定位失败原因。
    ///
    /// 主要功能：把不可用、未授权、超时和系统错误统一成命令回执可用的 code/message。
    enum LocationError: Error {
        case unavailable
        case denied
        case timeout
        case failed(String)

        var code: String {
            switch self {
            case .unavailable:
                return "location_unavailable"
            case .denied:
                return "location_denied"
            case .timeout:
                return "location_timeout"
            case .failed:
                return "location_failed"
            }
        }

        var message: String {
            switch self {
            case .unavailable:
                return "device location services are unavailable"
            case .denied:
                return "device location authorization was denied"
            case .timeout:
                return "device location request timed out"
            case let .failed(message):
                return message
            }
        }
    }

    private let manager = CLLocationManager()
    private var authorizationContinuation: CheckedContinuation<CLAuthorizationStatus, Never>?
    private var locationContinuation: CheckedContinuation<CLLocation, Error>?

    override init() {
        super.init()
        manager.delegate = self
    }

    /// 获取一次当前位置并转换成协议 payload。
    ///
    /// 参数：`highAccuracy` 对应 server 的 `high_accuracy`；`timeoutMS` 对应 `timeout_ms`。
    /// 返回值：与 JavaScript SDK 对齐的 `location` 字段字典。
    /// 异常情况：授权被拒或定位失败时抛出 `LocationError`。
    func currentLocation(highAccuracy: Bool, timeoutMS: Int) async throws -> RealtimeAgentLocationPayload {
        guard CLLocationManager.locationServicesEnabled() else {
            throw LocationError.unavailable
        }
        let status = await ensureAuthorization()
        guard status == .authorizedWhenInUse || status == .authorizedAlways else {
            throw LocationError.denied
        }
        manager.desiredAccuracy = highAccuracy ? kCLLocationAccuracyBest : kCLLocationAccuracyHundredMeters
        let location = try await requestOnce(timeoutMS: timeoutMS)
        return RealtimeAgentLocationPayload(fields: Self.payload(from: location))
    }

    private func ensureAuthorization() async -> CLAuthorizationStatus {
        let current = manager.authorizationStatus
        guard current == .notDetermined else { return current }
        return await withCheckedContinuation { continuation in
            authorizationContinuation = continuation
            manager.requestWhenInUseAuthorization()
        }
    }

    private func requestOnce(timeoutMS: Int) async throws -> CLLocation {
        try await withCheckedThrowingContinuation { continuation in
            locationContinuation = continuation
            manager.requestLocation()
            let nanoseconds = UInt64(max(timeoutMS, 1)) * 1_000_000
            Task { @MainActor [weak self] in
                try? await Task.sleep(nanoseconds: nanoseconds)
                self?.finishLocation(.failure(LocationError.timeout))
            }
        }
    }

    private func finishLocation(_ result: Result<CLLocation, Error>) {
        guard let continuation = locationContinuation else { return }
        locationContinuation = nil
        continuation.resume(with: result)
    }

    nonisolated func locationManagerDidChangeAuthorization(_: CLLocationManager) {
        MainActor.assumeIsolated {
            guard let continuation = authorizationContinuation else { return }
            authorizationContinuation = nil
            continuation.resume(returning: manager.authorizationStatus)
        }
    }

    nonisolated func locationManager(_: CLLocationManager, didUpdateLocations locations: [CLLocation]) {
        MainActor.assumeIsolated {
            guard let location = locations.last else { return }
            finishLocation(.success(location))
        }
    }

    nonisolated func locationManager(_: CLLocationManager, didFailWithError error: Error) {
        MainActor.assumeIsolated {
            finishLocation(.failure(LocationError.failed(error.localizedDescription)))
        }
    }

    private static func payload(from location: CLLocation) -> [String: Any] {
        [
            "latitude": location.coordinate.latitude,
            "longitude": location.coordinate.longitude,
            "accuracy": location.horizontalAccuracy >= 0 ? location.horizontalAccuracy : NSNull(),
            "altitude": location.verticalAccuracy >= 0 ? location.altitude : NSNull(),
            "altitude_accuracy": location.verticalAccuracy >= 0 ? location.verticalAccuracy : NSNull(),
            "heading": location.course >= 0 ? location.course : NSNull(),
            "speed": location.speed >= 0 ? location.speed : NSNull(),
            "timestamp_ms": Int64(location.timestamp.timeIntervalSince1970 * 1000),
            "source": "ios_core_location",
        ]
    }
}
#endif
