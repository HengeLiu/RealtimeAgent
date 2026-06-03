package com.audiochat.phone.ui

import android.annotation.SuppressLint
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.audiochat.phone.ble.BleProvisioner
import com.audiochat.phone.ble.ProvisionStep

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun ProvisionScreen(
    bleProvisioner: BleProvisioner,
    onDone: () -> Unit
) {
    // Only these are local UI state — everything else lives in BleProvisioner
    var ssid by remember { mutableStateOf("") }
    var password by remember { mutableStateOf("") }
    var serverHost by remember { mutableStateOf("192.168.31.8") }
    var serverPort by remember { mutableStateOf("8766") }

    // Read step from BleProvisioner — survives recomposition
    val step = bleProvisioner.step
    val statusMessage = bleProvisioner.statusMessage
    val errorMessage = bleProvisioner.errorMessage

    Column(
        modifier = Modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(16.dp)
    ) {
        Text(
            text = "眼镜配网",
            fontSize = 24.sp,
            fontWeight = FontWeight.Bold,
            color = TextPrimary
        )

        StepIndicator(step)

        when (step) {
            ProvisionStep.SCANNING -> {
                ScanningSection(
                    bleProvisioner = bleProvisioner,
                    onFound = { _, _ -> /* step managed by BleProvisioner */ }
                )
            }

            ProvisionStep.FOUND_DEVICE -> {
                FoundDeviceSection(
                    deviceName = bleProvisioner.foundDeviceName,
                    onConnect = {
                        bleProvisioner.foundDevice?.let { device ->
                            bleProvisioner.onConnectRequested()
                            bleProvisioner.connect(device) {
                                // Connected callback — step managed by BleProvisioner
                            }
                        }
                    },
                    onRescan = {
                        bleProvisioner.resetProvisioning()
                    }
                )
            }

            ProvisionStep.CONNECTING -> {
                SendingSection(statusMessage = "正在连接设备...")
            }

            ProvisionStep.ENTER_CREDENTIALS -> {
                EnterCredentialsSection(
                    ssid = ssid,
                    onSsidChange = { ssid = it },
                    password = password,
                    onPasswordChange = { password = it },
                    serverHost = serverHost,
                    onServerHostChange = { serverHost = it },
                    serverPort = serverPort,
                    onServerPortChange = { serverPort = it },
                    onSubmit = {
                        if (ssid.isBlank()) {
                            return@EnterCredentialsSection
                        }
                        bleProvisioner.observeStatus { /* status handled by BleProvisioner */ }
                        bleProvisioner.sendCredentials(
                            ssid = ssid,
                            pass = password,
                            serverHost = serverHost,
                            serverPort = serverPort.toIntOrNull() ?: 8766
                        )
                    }
                )
            }

            ProvisionStep.SENDING -> {
                SendingSection(statusMessage = statusMessage.ifBlank { "正在配网，请稍候..." })
            }

            ProvisionStep.SUCCESS -> {
                SuccessSection(
                    message = statusMessage.ifBlank { "配网已启动" },
                    onDone = onDone
                )
            }

            ProvisionStep.FAILED -> {
                FailedSection(
                    errorMessage = errorMessage.ifBlank { "未知错误" },
                    onRetry = { bleProvisioner.resetProvisioning() },
                    onDone = onDone
                )
            }
        }
    }
}

@Composable
fun StepIndicator(step: ProvisionStep) {
    val steps = listOf("搜索设备" to ProvisionStep.SCANNING, "连接设备" to ProvisionStep.CONNECTING,
        "WiFi配置" to ProvisionStep.ENTER_CREDENTIALS, "配网中" to ProvisionStep.SENDING)
    Row(
        modifier = Modifier.fillMaxWidth(),
        horizontalArrangement = Arrangement.SpaceEvenly
    ) {
        steps.forEach { (name, s) ->
            val isActive = step.ordinal >= s.ordinal
            Text(
                text = name,
                color = if (isActive) AccentBlue else TextSecondary,
                fontSize = 12.sp,
                fontWeight = if (step == s) FontWeight.Bold else FontWeight.Normal
            )
        }
    }
}

@SuppressLint("MissingPermission")
@Composable
fun ScanningSection(
    bleProvisioner: BleProvisioner,
    onFound: (String, android.bluetooth.BluetoothDevice) -> Unit
) {
    var isScanning by remember { mutableStateOf(false) }
    val context = androidx.compose.ui.platform.LocalContext.current
    val permissionLauncher = androidx.activity.compose.rememberLauncherForActivityResult(
        contract = androidx.activity.result.contract.ActivityResultContracts.RequestMultiplePermissions()
    ) { permissions ->
        val allGranted = permissions.values.all { it }
        if (allGranted) {
            isScanning = true
            bleProvisioner.startScan { name, device ->
                isScanning = false
                onFound(name, device)
            }
        }
    }

    fun startScanWithPermission() {
        val perms = if (android.os.Build.VERSION.SDK_INT >= android.os.Build.VERSION_CODES.S) {
            arrayOf(
                android.Manifest.permission.BLUETOOTH_SCAN,
                android.Manifest.permission.BLUETOOTH_CONNECT,
                android.Manifest.permission.ACCESS_FINE_LOCATION
            )
        } else {
            arrayOf(android.Manifest.permission.ACCESS_FINE_LOCATION)
        }
        permissionLauncher.launch(perms)
    }

    DebugCard {
        Icon(
            Icons.Default.BluetoothSearching,
            contentDescription = "蓝牙搜索图标",
            tint = AccentBlue,
            modifier = Modifier.size(48.dp).align(Alignment.CenterHorizontally)
        )
        Spacer(Modifier.height(16.dp))
        Text(
            text = if (isScanning) "正在搜索眼镜设备..." else "点击下方按钮开始搜索",
            color = TextPrimary,
            fontSize = 16.sp,
            modifier = Modifier.align(Alignment.CenterHorizontally)
        )
        Spacer(Modifier.height(16.dp))
        Button(
            onClick = { startScanWithPermission() },
            enabled = !isScanning,
            modifier = Modifier
                .fillMaxWidth()
                .height(56.dp)
                .semantics { contentDescription = if (isScanning) "正在搜索设备" else "开始搜索眼镜设备" },
            shape = RoundedCornerShape(12.dp)
        ) {
            Icon(Icons.Default.Search, contentDescription = null)
            Spacer(Modifier.width(8.dp))
            Text(if (isScanning) "搜索中..." else "搜索设备", fontSize = 18.sp)
        }
    }
}

@SuppressLint("MissingPermission")
@Composable
fun FoundDeviceSection(
    deviceName: String,
    onConnect: () -> Unit,
    onRescan: () -> Unit
) {
    DebugCard {
        Icon(
            Icons.Default.CheckCircle,
            contentDescription = "找到设备图标",
            tint = AccentGreen,
            modifier = Modifier.size(48.dp).align(Alignment.CenterHorizontally)
        )
        Spacer(Modifier.height(8.dp))
        Text(
            text = "找到设备",
            color = AccentGreen,
            fontSize = 16.sp,
            fontWeight = FontWeight.Bold,
            modifier = Modifier.align(Alignment.CenterHorizontally)
        )
        Text(
            text = deviceName,
            color = TextPrimary,
            fontSize = 20.sp,
            fontWeight = FontWeight.Bold,
            modifier = Modifier.align(Alignment.CenterHorizontally)
        )
        Spacer(Modifier.height(16.dp))
        Button(
            onClick = onConnect,
            modifier = Modifier
                .fillMaxWidth()
                .height(56.dp)
                .semantics { contentDescription = "连接设备 $deviceName" },
            shape = RoundedCornerShape(12.dp)
        ) {
            Icon(Icons.Default.Link, contentDescription = null)
            Spacer(Modifier.width(8.dp))
            Text("连接此设备", fontSize = 18.sp)
        }
        Spacer(Modifier.height(8.dp))
        OutlinedButton(
            onClick = onRescan,
            modifier = Modifier.fillMaxWidth().height(48.dp),
            shape = RoundedCornerShape(12.dp)
        ) {
            Text("重新搜索")
        }
    }
}

@Composable
fun EnterCredentialsSection(
    ssid: String, onSsidChange: (String) -> Unit,
    password: String, onPasswordChange: (String) -> Unit,
    serverHost: String, onServerHostChange: (String) -> Unit,
    serverPort: String, onServerPortChange: (String) -> Unit,
    onSubmit: () -> Unit
) {
    DebugCard {
        Text("WiFi 配置", color = AccentBlue, fontWeight = FontWeight.Bold)
        Spacer(Modifier.height(8.dp))

        OutlinedTextField(
            value = ssid,
            onValueChange = onSsidChange,
            label = { Text("WiFi 名称") },
            modifier = Modifier
                .fillMaxWidth()
                .semantics { contentDescription = "输入WiFi网络名称" },
            singleLine = true
        )
        Spacer(Modifier.height(8.dp))

        OutlinedTextField(
            value = password,
            onValueChange = onPasswordChange,
            label = { Text("WiFi 密码") },
            modifier = Modifier
                .fillMaxWidth()
                .semantics { contentDescription = "输入WiFi密码" },
            singleLine = true
        )

        Spacer(Modifier.height(16.dp))
        Text("服务器 (可选)", color = TextSecondary, fontSize = 12.sp)
        Spacer(Modifier.height(4.dp))

        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            OutlinedTextField(
                value = serverHost,
                onValueChange = onServerHostChange,
                label = { Text("服务器地址") },
                modifier = Modifier.weight(1f),
                singleLine = true
            )
            OutlinedTextField(
                value = serverPort,
                onValueChange = onServerPortChange,
                label = { Text("端口") },
                modifier = Modifier.width(100.dp),
                singleLine = true
            )
        }

        Spacer(Modifier.height(16.dp))
        Button(
            onClick = onSubmit,
            modifier = Modifier
                .fillMaxWidth()
                .height(56.dp)
                .semantics { contentDescription = "开始配网" },
            shape = RoundedCornerShape(12.dp)
        ) {
            Icon(Icons.Default.FlashOn, contentDescription = null)
            Spacer(Modifier.width(8.dp))
            Text("开始配网", fontSize = 18.sp)
        }
    }
}

@Composable
fun SendingSection(statusMessage: String) {
    DebugCard {
        CircularProgressIndicator(
            modifier = Modifier.size(48.dp).align(Alignment.CenterHorizontally),
            color = AccentBlue
        )
        Spacer(Modifier.height(16.dp))
        Text(
            text = statusMessage,
            color = TextPrimary,
            fontSize = 16.sp,
            modifier = Modifier.align(Alignment.CenterHorizontally)
        )
    }
}

@Composable
fun SuccessSection(message: String, onDone: () -> Unit) {
    DebugCard {
        Icon(
            Icons.Default.CheckCircle,
            contentDescription = "配网成功图标",
            tint = AccentGreen,
            modifier = Modifier.size(64.dp).align(Alignment.CenterHorizontally)
        )
        Spacer(Modifier.height(16.dp))
        Text(
            text = "配网已启动",
            color = AccentGreen,
            fontSize = 24.sp,
            fontWeight = FontWeight.Bold,
            modifier = Modifier.align(Alignment.CenterHorizontally)
        )
        Text(
            text = message,
            color = TextSecondary,
            modifier = Modifier.align(Alignment.CenterHorizontally)
        )
        Spacer(Modifier.height(24.dp))
        Button(
            onClick = onDone,
            modifier = Modifier
                .fillMaxWidth()
                .height(56.dp)
                .semantics { contentDescription = "完成配网，返回主页" },
            shape = RoundedCornerShape(12.dp)
        ) {
            Text("完成", fontSize = 18.sp)
        }
    }
}

@Composable
fun FailedSection(
    errorMessage: String,
    onRetry: () -> Unit,
    onDone: () -> Unit
) {
    DebugCard {
        Icon(
            Icons.Default.Error,
            contentDescription = "配网失败图标",
            tint = AccentRed,
            modifier = Modifier.size(64.dp).align(Alignment.CenterHorizontally)
        )
        Spacer(Modifier.height(16.dp))
        Text(
            text = "配网失败",
            color = AccentRed,
            fontSize = 24.sp,
            fontWeight = FontWeight.Bold,
            modifier = Modifier.align(Alignment.CenterHorizontally)
        )
        Text(
            text = errorMessage,
            color = TextSecondary,
            modifier = Modifier.align(Alignment.CenterHorizontally)
        )
        Spacer(Modifier.height(24.dp))
        Button(
            onClick = onRetry,
            modifier = Modifier
                .fillMaxWidth()
                .height(56.dp)
                .semantics { contentDescription = "重试配网" },
            shape = RoundedCornerShape(12.dp)
        ) {
            Icon(Icons.Default.Refresh, contentDescription = null)
            Spacer(Modifier.width(8.dp))
            Text("重试", fontSize = 18.sp)
        }
        Spacer(Modifier.height(8.dp))
        OutlinedButton(
            onClick = onDone,
            modifier = Modifier.fillMaxWidth().height(48.dp),
            shape = RoundedCornerShape(12.dp)
        ) {
            Text("返回")
        }
    }
}
