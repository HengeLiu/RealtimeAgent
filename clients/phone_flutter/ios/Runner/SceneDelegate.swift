import Flutter
import Network
import UIKit

class SceneDelegate: FlutterSceneDelegate {
  private let channelName = "nextgen.native_network_probe"

  override func scene(
    _ scene: UIScene,
    willConnectTo session: UISceneSession,
    options connectionOptions: UIScene.ConnectionOptions
  ) {
    super.scene(scene, willConnectTo: session, options: connectionOptions)
    guard let flutterController = window?.rootViewController as? FlutterViewController else {
      return
    }
    let channel = FlutterMethodChannel(name: channelName, binaryMessenger: flutterController.binaryMessenger)
    channel.setMethodCallHandler { [weak self] call, result in
      guard let self else {
        result(FlutterError(code: "scene_delegate_missing", message: "SceneDelegate 已释放", details: nil))
        return
      }
      self.handle(call: call, result: result)
    }
  }

  private func handle(call: FlutterMethodCall, result: @escaping FlutterResult) {
    switch call.method {
    case "probeServer":
      guard
        let args = call.arguments as? [String: Any],
        let urlString = args["url"] as? String
      else {
        result(FlutterError(code: "bad_args", message: "缺少 url 参数", details: nil))
        return
      }
      probeServer(urlString: urlString, result: result)
    default:
      result(FlutterMethodNotImplemented)
    }
  }

  private func probeServer(urlString: String, result: @escaping FlutterResult) {
    guard let url = URL(string: urlString), let host = url.host else {
      result(FlutterError(code: "bad_url", message: "url 无法解析", details: urlString))
      return
    }
    let portValue = url.port ?? (url.scheme == "https" ? 443 : 80)
    guard let port = NWEndpoint.Port(rawValue: UInt16(portValue)) else {
      result(FlutterError(code: "bad_port", message: "端口无效", details: portValue))
      return
    }

    let group = DispatchGroup()
    var payload: [String: Any] = [
      "target": urlString,
      "host": host,
      "port": portValue
    ]

    group.enter()
    performTcpProbe(host: host, port: port) { probeResult in
      payload["native_socket_connect"] = probeResult.status
      if let error = probeResult.error {
        payload["native_socket_error"] = error
      }
      group.leave()
    }

    group.enter()
    performHttpProbe(url: url) { probeResult in
      if let statusCode = probeResult.statusCode {
        payload["native_http_status"] = statusCode
      }
      if let reason = probeResult.reason {
        payload["native_http_reason"] = reason
      }
      if let error = probeResult.error {
        payload["native_http_error"] = error
      }
      group.leave()
    }

    group.notify(queue: .main) {
      result(payload)
    }
  }

  private func performTcpProbe(
    host: String,
    port: NWEndpoint.Port,
    completion: @escaping ((status: String, error: String?)) -> Void
  ) {
    let connection = NWConnection(host: NWEndpoint.Host(host), port: port, using: .tcp)
    let queue = DispatchQueue(label: "nextgen.native_network_probe.tcp")
    var finished = false

    connection.stateUpdateHandler = { state in
      guard !finished else { return }
      switch state {
      case .ready:
        finished = true
        completion(("ok", nil))
        connection.cancel()
      case .failed(let error):
        finished = true
        completion(("error", error.localizedDescription))
        connection.cancel()
      case .cancelled:
        if !finished {
          finished = true
          completion(("cancelled", nil))
        }
      default:
        break
      }
    }

    connection.start(queue: queue)
    queue.asyncAfter(deadline: .now() + 4) {
      guard !finished else { return }
      finished = true
      completion(("timeout", "TCP probe timed out after 4 seconds"))
      connection.cancel()
    }
  }

  private func performHttpProbe(
    url: URL,
    completion: @escaping ((statusCode: Int?, reason: String?, error: String?)) -> Void
  ) {
    let configuration = URLSessionConfiguration.ephemeral
    configuration.timeoutIntervalForRequest = 4
    configuration.timeoutIntervalForResource = 4
    let session = URLSession(configuration: configuration)
    let task = session.dataTask(with: url) { _, response, error in
      defer { session.finishTasksAndInvalidate() }
      if let error {
        completion((nil, nil, error.localizedDescription))
        return
      }
      if let httpResponse = response as? HTTPURLResponse {
        completion((httpResponse.statusCode, HTTPURLResponse.localizedString(forStatusCode: httpResponse.statusCode), nil))
        return
      }
      completion((nil, nil, "未收到 HTTPURLResponse"))
    }
    task.resume()
  }
}
