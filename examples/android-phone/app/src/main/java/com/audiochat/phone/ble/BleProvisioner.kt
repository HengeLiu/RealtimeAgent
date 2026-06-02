package com.audiochat.phone.ble

import android.annotation.SuppressLint
import android.bluetooth.*
import android.bluetooth.le.*
import android.content.Context
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.util.Log
import java.util.UUID

/**
 * BLE provisioner for ESP32 Glass device.
 * Scans for devices with name prefix "Glass-", connects, and sends WiFi credentials.
 */
class BleProvisioner(private val context: Context) {

    companion object {
        private const val TAG = "BleProvisioner"
    }

    private val bluetoothManager = context.getSystemService(Context.BLUETOOTH_SERVICE) as BluetoothManager
    private val bluetoothAdapter: BluetoothAdapter? = bluetoothManager.adapter
    private var bluetoothLeScanner: BluetoothLeScanner? = null
    private var gatt: BluetoothGatt? = null
    private val handler = Handler(Looper.getMainLooper())

    // State
    private var isScanning = false
    private var statusCallback: ((String) -> Unit)? = null
    private var connectedCallback: (() -> Unit)? = null

    // Characteristics discovered
    private var ssidChar: BluetoothGattCharacteristic? = null
    private var passChar: BluetoothGattCharacteristic? = null
    private var pairCodeChar: BluetoothGattCharacteristic? = null
    private var serverChar: BluetoothGattCharacteristic? = null
    private var statusChar: BluetoothGattCharacteristic? = null

    // Credential sending state
    private var credentialsToSend: Credentials? = null
    private var writeQueue = mutableListOf<Pair<BluetoothGattCharacteristic, ByteArray>>()

    data class Credentials(
        val ssid: String,
        val password: String,
        val pairingCode: String,
        val serverHost: String,
        val serverPort: Int
    )

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

        val filters = listOf(
            ScanFilter.Builder()
                .setDeviceName(BleConstants.DEVICE_NAME_PREFIX)
                .build()
        )

        val settings = ScanSettings.Builder()
            .setScanMode(ScanSettings.SCAN_MODE_LOW_LATENCY)
            .build()

        val callback = object : ScanCallback() {
            override fun onScanResult(callbackType: Int, result: ScanResult) {
                val device = result.device
                val name = device.name ?: return
                if (name.startsWith(BleConstants.DEVICE_NAME_PREFIX)) {
                    Log.i(TAG, "Found device: $name (${device.address})")
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
    fun sendCredentials(ssid: String, pass: String, code: String, serverHost: String, serverPort: Int) {
        credentialsToSend = Credentials(ssid, pass, code, serverHost, serverPort)

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
        gatt?.disconnect()
        gatt?.close()
        gatt = null
        ssidChar = null
        passChar = null
        pairCodeChar = null
        serverChar = null
        statusChar = null
        writeQueue.clear()
        credentialsToSend = null
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
        pairCodeChar?.let {
            writeQueue.add(it to creds.pairingCode.toByteArray())
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
                    gatt.discoverServices()
                }
                BluetoothProfile.STATE_DISCONNECTED -> {
                    Log.i(TAG, "GATT disconnected")
                    handler.post { statusCallback?.invoke("disconnected") }
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
            pairCodeChar = service.getCharacteristic(BleConstants.CHAR_PAIR_CODE)
            serverChar = service.getCharacteristic(BleConstants.CHAR_SERVER)
            statusChar = service.getCharacteristic(BleConstants.CHAR_STATUS)

            Log.i(TAG, "Services discovered, characteristics found")

            handler.post {
                connectedCallback?.invoke()
                enableStatusNotify()

                // If credentials already queued, send them
                if (credentialsToSend != null) {
                    writeCredentialsSequentially()
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
                handler.post { statusCallback?.invoke(status) }
            }
        }

        override fun onDescriptorWrite(gatt: BluetoothGatt, descriptor: BluetoothGattDescriptor, status: Int) {
            if (status == BluetoothGatt.GATT_SUCCESS) {
                Log.i(TAG, "Descriptor write success")
            }
        }
    }
}
