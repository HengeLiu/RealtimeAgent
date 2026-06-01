import Foundation

#if canImport(AVFoundation)
import AVFoundation
#endif

enum RealtimeAgentHardwarePermissionRequester {
    static func request(audioInput: AudioInput, camera: Camera) async throws -> HardwarePermissionStatus {
        async let microphone = requestMicrophoneIfNeeded(enabled: audioInput.enabled)
        async let video = requestCameraIfNeeded(enabled: camera.enabled)
        return HardwarePermissionStatus(microphone: await microphone, camera: await video)
    }

    private static func requestMicrophoneIfNeeded(enabled: Bool) async -> HardwarePermissionState {
        guard enabled else { return .notRequired }
        #if canImport(AVFoundation)
        return await requestAVCapturePermission(for: .audio)
        #else
        return .unavailable
        #endif
    }

    private static func requestCameraIfNeeded(enabled: Bool) async -> HardwarePermissionState {
        guard enabled else { return .notRequired }
        #if canImport(AVFoundation)
        return await requestAVCapturePermission(for: .video)
        #else
        return .unavailable
        #endif
    }

    #if canImport(AVFoundation)
    private static func requestAVCapturePermission(for mediaType: AVMediaType) async -> HardwarePermissionState {
        switch AVCaptureDevice.authorizationStatus(for: mediaType) {
        case .authorized:
            return .granted
        case .denied:
            return .denied
        case .restricted:
            return .restricted
        case .notDetermined:
            let granted = await withCheckedContinuation { continuation in
                AVCaptureDevice.requestAccess(for: mediaType) { granted in
                    continuation.resume(returning: granted)
                }
            }
            return granted ? .granted : .denied
        @unknown default:
            return .unavailable
        }
    }
    #endif
}
