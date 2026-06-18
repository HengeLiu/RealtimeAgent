import CoreLocation
import CoreMotion
import Foundation

/// 手机 GPS 定位与姿态读取工具。
///
/// 主要功能：在 App 层用 CoreLocation 取一次 GPS 定位、用 CoreMotion 取一次设备姿态(attitude)，
/// 供调试页打印；与 SDK 内置的 `device.location.get_current` 命令处理相互独立。
/// 主要逻辑：所有 CLLocationManager / CMMotionManager 操作和回调都保持在主线程，避免缺少 run loop
/// 导致定位回调不触发，也避免跨线程访问续延。
@MainActor
final class DeviceSensorsReader: NSObject, CLLocationManagerDelegate {
    /// 读取失败原因。
    enum SensorsError: LocalizedError {
        case locationServicesDisabled
        case locationDenied
        case locationTimeout
        case locationFailed(String)
        case motionUnavailable
        case motionTimeout
        case motionFailed(String)

        var errorDescription: String? {
            switch self {
            case .locationServicesDisabled:
                return "系统定位服务未开启"
            case .locationDenied:
                return "定位权限被拒绝"
            case .locationTimeout:
                return "定位请求超时"
            case let .locationFailed(message):
                return message
            case .motionUnavailable:
                return "设备不支持姿态(device motion)"
            case .motionTimeout:
                return "姿态读取超时"
            case let .motionFailed(message):
                return message
            }
        }
    }

    private let locationManager = CLLocationManager()
    private let motionManager = CMMotionManager()
    private var authorizationContinuation: CheckedContinuation<CLAuthorizationStatus, Never>?
    private var locationContinuation: CheckedContinuation<CLLocation, Error>?

    override init() {
        super.init()
        locationManager.delegate = self
    }

    /// 取一次当前 GPS 定位。
    ///
    /// 参数：`timeoutMS` 为定位超时(毫秒)。
    /// 异常情况：服务未开启、授权被拒、超时或系统错误时抛出 `SensorsError`。
    func currentLocation(timeoutMS: Int = 8000) async throws -> CLLocation {
        guard CLLocationManager.locationServicesEnabled() else {
            throw SensorsError.locationServicesDisabled
        }
        let status = await ensureAuthorization()
        guard status == .authorizedWhenInUse || status == .authorizedAlways else {
            throw SensorsError.locationDenied
        }
        locationManager.desiredAccuracy = kCLLocationAccuracyBest
        return try await withCheckedThrowingContinuation { continuation in
            locationContinuation = continuation
            locationManager.requestLocation()
            DispatchQueue.main.asyncAfter(deadline: .now() + .milliseconds(timeoutMS)) { [weak self] in
                self?.finishLocation(.failure(SensorsError.locationTimeout))
            }
        }
    }

    /// 取一次当前设备姿态。
    ///
    /// 参数：`timeoutMS` 为等待首帧 device motion 的超时(毫秒)。
    /// 返回值：包含 attitude(roll/pitch/yaw)、gravity、rotationRate 的 `CMDeviceMotion`。
    /// 异常情况：设备不支持、超时或系统错误时抛出 `SensorsError`。
    func currentDeviceMotion(timeoutMS: Int = 2000) async throws -> CMDeviceMotion {
        guard motionManager.isDeviceMotionAvailable else {
            throw SensorsError.motionUnavailable
        }
        if let motion = motionManager.deviceMotion {
            return motion
        }
        motionManager.deviceMotionUpdateInterval = 1.0 / 30.0
        return try await withCheckedThrowingContinuation { continuation in
            var finished = false
            let finish: (Result<CMDeviceMotion, Error>) -> Void = { [weak self] result in
                if finished { return }
                finished = true
                self?.motionManager.stopDeviceMotionUpdates()
                continuation.resume(with: result)
            }
            motionManager.startDeviceMotionUpdates(to: .main) { motion, error in
                if let motion {
                    finish(.success(motion))
                } else if let error {
                    finish(.failure(SensorsError.motionFailed(error.localizedDescription)))
                }
            }
            DispatchQueue.main.asyncAfter(deadline: .now() + .milliseconds(timeoutMS)) {
                finish(.failure(SensorsError.motionTimeout))
            }
        }
    }

    private func ensureAuthorization() async -> CLAuthorizationStatus {
        let current = locationManager.authorizationStatus
        guard current == .notDetermined else { return current }
        return await withCheckedContinuation { continuation in
            authorizationContinuation = continuation
            locationManager.requestWhenInUseAuthorization()
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
            continuation.resume(returning: locationManager.authorizationStatus)
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
            finishLocation(.failure(SensorsError.locationFailed(error.localizedDescription)))
        }
    }
}
