package com.audiochat.phone.ble

import android.annotation.SuppressLint
import android.bluetooth.*
import android.bluetooth.le.*
import android.content.Context
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.util.Log
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONObject
import java.util.UUID
import java.util.concurrent.TimeUnit

enum class ProvisionStep {
    SCANNING,
    FOUND_DEVICE,
    ENTER_CREDENTIALS,
    CONNECTING,
    SENDING,
    SUCCESS,
    FAILED
}

/**
 * BLE provisioner for ESP32 Glass device.
 * Scans for devices with name prefix "Glass-", connects, and sends WiFi credentials.
 */
class BleProvisioner(private val context: Context, private val userId: String = "app-user") {

    companion object {
        private const val TAG = "BleProvisioner"
    }

    private val bluetoothManager = context.getSystemService(Context.BLUETOOTH_SERVICE) as BluetoothManager
    private val bluetoothAdapter: BluetoothAdapter? = bluetoothManager.adapter
    private var bluetoothLeScanner: BluetoothLeScanner? = null
    private var gatt: BluetoothGatt? = null
    private val handler = Handler(Looper.getMainLooper())

    // Observable state (survives recomposition since BleProvisioner is hoisted)
    var step by mutableStateOf(ProvisionStep.SCANNING)
        private set
    var statusMessage by mutableStateOf("")
        private set
    var errorMessage by mutableStateOf("")
        private set
    var foundDeviceName: String = ""
        private set
    var foundDevice: BluetoothDevice? = null
        private set
    var isConnected: Boolean = false
        private set
    private var isScanning = false
    private var statusCallback: ((String) -> Unit)? = null
    private var connectedCallback: (() -> Unit)? = null

    // Characteristics discovered
    private var ssidChar: BluetoothGattCharacteristic? = null
    private var passChar: BluetoothGattCharacteristic? = null
    private var serverChar: BluetoothGattCharacteristic? = null
    private var statusChar: BluetoothGattCharacteristic? = null

    // Credential sending state
    private var credentialsToSend: Credentials? = null
    private var writeQueue = mutableListOf<Pair<BluetoothGattCharacteristic, ByteArray>>()
    private var lastStatus: String? = null
    private var pollingRunnable: Runnable? = null

    private val httpClient = OkHttpClient.Builder()
        .connectTimeout(5, TimeUnit.SECONDS)
        .readTimeout(5, TimeUnit.SECONDS)
        .build()

    data class Credentials(
        val ssid: String,
        val password: String,
        val serverHost: String,
        val serverPort: Int
    )

    fun resetProvisioning() {
        disconnect()
        pollingRunnable?.let { handler.removeCallbacks(it) }
        pollingRunnable = null
        step = ProvisionStep.SCANNING
        statusMessage = ""
        errorMessage = ""
        foundDeviceName = ""
        foundDevice = null
        isConnected = false
        lastStatus = null
    }

    fun onDeviceFound(name: String, device: BluetoothDevice) {
        foundDeviceName = name
        foundDevice = device
        if (step == ProvisionStep.SCANNING) {
            step = ProvisionStep.FOUND_DEVICE
        }
    }

    fun onConnectRequested() {
        step = ProvisionStep.CONNECTING
    }

    fun handleBleStatus(status: String) {
        lastStatus = status
        statusMessage = when (status) {
            "connecting" -> "正在连接WiFi..."
            "wifi_ok" -> "WiFi连接成功，正在配对..."
            "pair_ok" -> {
                step = ProvisionStep.SUCCESS
                "配网成功！"
            }
            "prov_started" -> {
                step = ProvisionStep.SENDING
                // Start polling server to detect when ESP32 connects
                val creds = credentialsToSend
                if (creds != null) {
                    startPollingForDevice(creds.serverHost, creds.serverPort)
                }
                "眼镜正在连接WiFi，请等待..."
            }
            "disconnected" -> {
                step = ProvisionStep.FAILED
                errorMessage = "设备连接断开，请重试"
                "连接断开"
            }
            else -> {
                if (status.startsWith("fail:")) {
                    step = ProvisionStep.FAILED
                    errorMessage = "配网失败: ${status.removePrefix("fail:")}"
                }
                status
            }
        }
    }

    private fun startPollingForDevice(serverHost: String, serverPort: Int) {
        // Use the BLE device name suffix (e.g., "Glass-a1b2" → "a1b2") to match hardware_id
        // This prevents binding the wrong device when multiple are being provisioned
        val nameSuffix = foundDeviceName.removePrefix(BleConstants.DEVICE_NAME_PREFIX).lowercase()
        val registerUrl = "http://$serverHost:$serverPort/api/device/registered"
        var attempts = 0
        val maxAttempts = 24  // 2 minutes (5s interval)

        pollingRunnable?.let { handler.removeCallbacks(it) }

        val runnable = object : Runnable {
            override fun run() {
                if (step != ProvisionStep.SENDING) return
                attempts++

                Thread {
                    try {
                        // Check if device registered on server
                        val request = Request.Builder().url(registerUrl).get().build()
                        val response = httpClient.newCall(request).execute()
                        val body = response.body?.string() ?: ""
                        response.close()

                        val json = JSONObject(body)
                        val devices = json.optJSONArray("devices")

                        // Find unbound device matching our BLE device name suffix
                        var foundHardwareId: String? = null
                        if (devices != null) {
                            for (i in 0 until devices.length()) {
                                val dev = devices.getJSONObject(i)
                                val hwId = dev.optString("hardware_id", "")
                                if (!dev.optBoolean("bound", false) && hwId.endsWith(nameSuffix)) {
                                    foundHardwareId = hwId
                                    break
                                }
                            }
                        }

                        if (foundHardwareId != null) {
                            // Device found — try to bind
                            val bindSuccess = bindDevice(serverHost, serverPort, foundHardwareId)
                            handler.post {
                                if (bindSuccess) {
                                    step = ProvisionStep.SUCCESS
                                    statusMessage = "眼镜已连接并绑定成功！"
                                } else {
                                    step = ProvisionStep.SUCCESS
                                    statusMessage = "眼镜已连接到服务器！（绑定待完成）"
                                }
                            }
                        } else if (attempts >= maxAttempts) {
                            handler.post {
                                step = ProvisionStep.FAILED
                                errorMessage = "眼镜未能连接到服务器，请检查WiFi是否正确"
                            }
                        } else {
                            handler.postDelayed(this, 5000)
                        }
                    } catch (e: Exception) {
                        Log.w(TAG, "Poll error: ${e.message}")
                        if (attempts >= maxAttempts) {
                            handler.post {
                                step = ProvisionStep.FAILED
                                errorMessage = "无法连接到服务器: ${e.message}"
                            }
                        } else {
                            handler.postDelayed(this, 5000)
                        }
                    }
                }.start()
            }
        }

        pollingRunnable = runnable
        handler.postDelayed(runnable, 5000)  // First check after 5s
    }

    private fun bindDevice(serverHost: String, serverPort: Int, hardwareId: String): Boolean {
        return try {
            val bindUrl = "http://$serverHost:$serverPort/api/device/bind"
            val bindBody = JSONObject().apply {
                put("hardware_id", hardwareId)
                put("user_id", userId)
            }
            val body = bindBody.toString().toRequestBody("application/json".toMediaType())
            val request = Request.Builder()
                .url(bindUrl)
                .post(body)
                .build()
            val response = httpClient.newCall(request).execute()
            val success = response.isSuccessful
            response.close()
            Log.i(TAG, "Bind result: $success")
            success
        } catch (e: Exception) {
            Log.w(TAG, "Bind error: ${e.message}")
            false
        }
    }

    fun isBluetoothEnabled(): Boolean {
        return bluetoothAdapter?.isEnabled == true
    }

    @SuppressLint("MissingPermission")
    fun startScan(onFound: (String, BluetoothDevice) -> Unit) {
        if (!isBluetoothEnabled()) {
            Log.e(TAG, "Bluetooth not enabled")
            return
        }

        bluetoothLeScanner = bluetoothAdapter?.bluetoothLeScanner
        if (bluetoothLeScanner == null) {
            Log.e(TAG, "BLE scanner not available")
            return
        }

        isScanning = true

        val filters = listOf<ScanFilter>()

        val settings = ScanSettings.Builder()
            .setScanMode(ScanSettings.SCAN_MODE_LOW_LATENCY)
            .build()

        val callback = object : ScanCallback() {
            override fun onScanResult(callbackType: Int, result: ScanResult) {
                val device = result.device
                val name = device.name ?: return
                if (name.startsWith(BleConstants.DEVICE_NAME_PREFIX)) {
                    Log.i(TAG, "Found device: $name (${device.address})")
                    onDeviceFound(name, device)
                    onFound(name, device)
                }
            }

            override fun onScanFailed(errorCode: Int) {
                Log.e(TAG, "Scan failed: $errorCode")
                isScanning = false
            }
        }

        bluetoothLeScanner?.startScan(filters, settings, callback)

        // Auto-stop after timeout
        handler.postDelayed({
            if (isScanning) {
                stopScan()
            }
        }, BleConstants.SCAN_TIMEOUT_MS)
    }

    @SuppressLint("MissingPermission")
    fun stopScan() {
        if (!isScanning) return
        isScanning = false
        try {
            bluetoothLeScanner?.stopScan(object : ScanCallback() {})
        } catch (e: Exception) {
            Log.w(TAG, "Stop scan error: ${e.message}")
        }
    }

    @SuppressLint("MissingPermission")
    fun connect(device: BluetoothDevice, onConnected: () -> Unit) {
        stopScan()
        connectedCallback = onConnected
        Log.i(TAG, "Connecting to ${device.name} (${device.address})")
        gatt = device.connectGatt(context, false, gattCallback, BluetoothDevice.TRANSPORT_LE)
    }

    @SuppressLint("MissingPermission")
    fun sendCredentials(ssid: String, pass: String, serverHost: String, serverPort: Int) {
        step = ProvisionStep.SENDING
        credentialsToSend = Credentials(ssid, pass, serverHost, serverPort)

        // If characteristics already discovered, send now
        if (ssidChar != null) {
            writeCredentialsSequentially()
        }
        // Otherwise, will send after services discovered
    }

    fun observeStatus(callback: (String) -> Unit) {
        statusCallback = callback
    }

    @SuppressLint("MissingPermission")
    fun disconnect() {
        pollingRunnable?.let { handler.removeCallbacks(it) }
        pollingRunnable = null
        gatt?.disconnect()
        gatt?.close()
        gatt = null
        ssidChar = null
        passChar = null
        serverChar = null
        statusChar = null
        writeQueue.clear()
        credentialsToSend = null
        lastStatus = null
    }

    @SuppressLint("MissingPermission")
    private fun writeCredentialsSequentially() {
        val creds = credentialsToSend ?: return
        val g = gatt ?: return

        writeQueue.clear()

        ssidChar?.let {
            writeQueue.add(it to creds.ssid.toByteArray())
        }
        passChar?.let {
            writeQueue.add(it to creds.password.toByteArray())
        }
        serverChar?.let {
            val serverInfo = "${creds.serverHost}:${creds.serverPort}"
            writeQueue.add(it to serverInfo.toByteArray())
        }

        writeNextInQueue()
    }

    @SuppressLint("MissingPermission")
    private fun writeNextInQueue() {
        if (writeQueue.isEmpty()) {
            Log.i(TAG, "All credentials written")
            return
        }

        val (char, value) = writeQueue.removeAt(0)
        val g = gatt ?: return

        char.value = value
        char.writeType = BluetoothGattCharacteristic.WRITE_TYPE_DEFAULT
        g.writeCharacteristic(char)
        Log.i(TAG, "Writing to ${char.uuid}: ${value.size} bytes")
    }

    @SuppressLint("MissingPermission")
    private fun enableStatusNotify() {
        val g = gatt ?: return
        val char = statusChar ?: return

        g.setCharacteristicNotification(char, true)
        val descriptor = char.getDescriptor(BleConstants.CCCD_UUID)
        if (descriptor != null) {
            descriptor.value = BluetoothGattDescriptor.ENABLE_NOTIFICATION_VALUE
            g.writeDescriptor(descriptor)
            Log.i(TAG, "Status notify enabled")
        }
    }

    private val gattCallback = object : BluetoothGattCallback() {
        override fun onConnectionStateChange(gatt: BluetoothGatt, status: Int, newState: Int) {
            when (newState) {
                BluetoothProfile.STATE_CONNECTED -> {
                    Log.i(TAG, "GATT connected, discovering services...")
                    isConnected = true
                    gatt.discoverServices()
                }
                BluetoothProfile.STATE_DISCONNECTED -> {
                    Log.i(TAG, "GATT disconnected (lastStatus=$lastStatus)")
                    isConnected = false
                    // If ESP32 sent "connecting" before disconnecting, it intentionally
                    // stopped BLE to start WiFi — not an error
                    if (lastStatus == "connecting") {
                        handleBleStatus("prov_started")
                        handler.post { statusCallback?.invoke("prov_started") }
                    } else if (step == ProvisionStep.SENDING || step == ProvisionStep.CONNECTING) {
                        handleBleStatus("disconnected")
                        handler.post { statusCallback?.invoke("disconnected") }
                    }
                }
            }
        }

        override fun onServicesDiscovered(gatt: BluetoothGatt, status: Int) {
            if (status != BluetoothGatt.GATT_SUCCESS) {
                Log.e(TAG, "Service discovery failed: $status")
                return
            }

            val service = gatt.getService(BleConstants.SERVICE_UUID)
            if (service == null) {
                Log.e(TAG, "Provisioning service not found")
                return
            }

            ssidChar = service.getCharacteristic(BleConstants.CHAR_SSID)
            passChar = service.getCharacteristic(BleConstants.CHAR_PASS)
            serverChar = service.getCharacteristic(BleConstants.CHAR_SERVER)
            statusChar = service.getCharacteristic(BleConstants.CHAR_STATUS)

            Log.i(TAG, "Services discovered, characteristics found")

            handler.post {
                connectedCallback?.invoke()
                connectedCallback = null  // Only fire once
                enableStatusNotify()

                // If credentials already queued, send them
                if (credentialsToSend != null) {
                    writeCredentialsSequentially()
                } else {
                    step = ProvisionStep.ENTER_CREDENTIALS
                }
            }
        }

        override fun onCharacteristicWrite(gatt: BluetoothGatt, characteristic: BluetoothGattCharacteristic, status: Int) {
            if (status == BluetoothGatt.GATT_SUCCESS) {
                Log.i(TAG, "Write success: ${characteristic.uuid}")
                writeNextInQueue()
            } else {
                Log.e(TAG, "Write failed: ${characteristic.uuid}, status=$status")
                handler.post { statusCallback?.invoke("fail:write") }
            }
        }

        override fun onCharacteristicChanged(gatt: BluetoothGatt, characteristic: BluetoothGattCharacteristic) {
            if (characteristic.uuid == BleConstants.CHAR_STATUS) {
                val status = String(characteristic.value)
                Log.i(TAG, "Status changed: $status")
                handler.post {
                    handleBleStatus(status)
                    statusCallback?.invoke(status)
                }
            }
        }

        override fun onDescriptorWrite(gatt: BluetoothGatt, descriptor: BluetoothGattDescriptor, status: Int) {
            if (status == BluetoothGatt.GATT_SUCCESS) {
                Log.i(TAG, "Descriptor write success")
            }
        }
    }
}
