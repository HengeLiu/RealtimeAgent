import Foundation

/// 手机端通用任务事件上报接口。
enum PhoneTaskEventReportAPI {
    /// 上报一次手机任务事件。
    static func report(
        taskID: String,
        phoneDeviceID: String,
        eventName: String,
        payload: [String: Any]
    ) async throws {
        guard let config = ReceiverAppConfig.load() else {
            throw URLError(.badURL)
        }
        guard let url = URL(string: "\(config.serverHTTPBaseURLString)/api/tasks/report-event") else {
            throw URLError(.badURL)
        }
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try JSONSerialization.data(withJSONObject: [
            "task_id": taskID,
            "phone_device_id": phoneDeviceID,
            "event_name": eventName,
            "payload": payload,
        ])

        let (data, response) = try await URLSession.shared.data(for: request)
        guard let httpResponse = response as? HTTPURLResponse else {
            throw URLError(.badServerResponse)
        }
        if httpResponse.statusCode != 200 {
            let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any]
            let errorObject = object?["error"] as? [String: Any]
            let message = errorObject?["message"] as? String ?? "服务端返回非成功状态"
            throw NSError(
                domain: "GlassesVideoReceiver.PhoneTaskEventReportAPI",
                code: httpResponse.statusCode,
                userInfo: [NSLocalizedDescriptionKey: message]
            )
        }
    }
}
