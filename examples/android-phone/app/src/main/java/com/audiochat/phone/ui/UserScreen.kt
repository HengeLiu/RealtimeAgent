package com.audiochat.phone.ui

import android.app.Activity
import android.util.Log
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
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.audiochat.phone.error.ErrorToastManager

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun UserScreen(viewModel: MainViewModel, uiState: PhoneUiState) {
    val context = LocalContext.current
    val activity = context as? Activity
    
    var hasTriggeredAuth by remember { mutableStateOf(false) }
    var shouldAutoAuth by remember { mutableStateOf(true) }

    LaunchedEffect(uiState.authError) {
        if (uiState.authError.isNotEmpty()) {
            ErrorToastManager.showError(context, uiState.authError)
            viewModel.clearAuthError()
            shouldAutoAuth = false
        }
    }

    LaunchedEffect(uiState.isLoggedIn, shouldAutoAuth) {
        if (!uiState.isLoggedIn && !hasTriggeredAuth && shouldAutoAuth && activity != null) {
            hasTriggeredAuth = true
            Log.i("UserScreen", "Auto triggering one-click auth")
            viewModel.aliyunAuthManager?.startOneClickAuth(activity, 10000) { success, token, message ->
                Log.i("UserScreen", "Auth callback: success=$success, tokenLen=${token?.length ?: 0}, msg=$message")
                if (success && token != null) {
                    viewModel.onAuthSuccess(token)
                } else {
                    viewModel.onAuthFailed(message)
                }
            }
        }
    }

    Column(
        modifier = Modifier
            .fillMaxSize()
            .padding(16.dp)
            .verticalScroll(rememberScrollState()),
        verticalArrangement = Arrangement.spacedBy(16.dp)
    ) {
        if (!uiState.isLoggedIn) {
            DebugCard {
                Column(
                    modifier = Modifier.fillMaxWidth(),
                    verticalArrangement = Arrangement.spacedBy(12.dp),
                    horizontalAlignment = Alignment.CenterHorizontally
                ) {
                    Icon(
                        Icons.Default.Person,
                        contentDescription = null,
                        modifier = Modifier.size(64.dp),
                        tint = AccentBlue
                    )
                    
                    Text(
                        text = "欢迎使用",
                        fontSize = 24.sp,
                        fontWeight = FontWeight.Bold,
                        color = TextPrimary
                    )
                    
                    Text(
                        text = "正在唤起一键登录...",
                        fontSize = 14.sp,
                        color = TextSecondary
                    )

                    if (!shouldAutoAuth && !uiState.isAuthLoading) {
                        Spacer(Modifier.height(8.dp))
                        Button(
                            onClick = {
                                shouldAutoAuth = true
                                hasTriggeredAuth = false
                            },
                            modifier = Modifier.fillMaxWidth(),
                            colors = ButtonDefaults.buttonColors(containerColor = AccentBlue),
                            shape = RoundedCornerShape(8.dp)
                        ) {
                            Icon(Icons.Default.Refresh, contentDescription = null)
                            Spacer(Modifier.width(8.dp))
                            Text("重试登录", fontSize = 16.sp)
                        }
                    }
                }
            }
        } else {
            DebugCard {
                Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.SpaceBetween,
                        verticalAlignment = Alignment.CenterVertically
                    ) {
                        Column {
                            Text(
                                text = "欢迎回来",
                                color = TextPrimary,
                                fontSize = 20.sp,
                                fontWeight = FontWeight.Bold
                            )
                            Text(
                                text = uiState.userPhone,
                                color = TextSecondary,
                                fontSize = 14.sp
                            )
                        }

                        Icon(
                            Icons.Default.Person,
                            contentDescription = null,
                            modifier = Modifier.size(48.dp),
                            tint = AccentBlue
                        )
                    }

                    Divider(color = BorderColor, thickness = 1.dp)

                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.spacedBy(8.dp)
                    ) {
                        StatusBadge("用户ID", uiState.userId, AccentGreen)
                        StatusBadge("设备ID", uiState.deviceId, AccentBlue)
                    }

                    Divider(color = BorderColor, thickness = 1.dp)

                    Button(
                        onClick = { viewModel.logout() },
                        modifier = Modifier.fillMaxWidth(),
                        colors = ButtonDefaults.buttonColors(containerColor = AccentRed),
                        shape = RoundedCornerShape(8.dp)
                    ) {
                        Icon(Icons.Default.Logout, contentDescription = null)
                        Spacer(Modifier.width(8.dp))
                        Text("退出登录", fontSize = 16.sp)
                    }
                }
            }

            DebugCard {
                Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
                    SectionTitle("连接设置")

                    OutlinedTextField(
                        value = uiState.serverUrl,
                        onValueChange = { newValue ->
                            viewModel.updateServerUrl(newValue)
                        },
                        label = { Text("服务器地址") },
                        modifier = Modifier.fillMaxWidth(),
                        singleLine = true,
                        colors = OutlinedTextFieldDefaults.colors(
                            focusedBorderColor = AccentBlue,
                            unfocusedBorderColor = BorderColor
                        )
                    )

                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.spacedBy(8.dp)
                    ) {
                        Button(
                            onClick = { viewModel.connect() },
                            modifier = Modifier.weight(1f),
                            enabled = !uiState.isConnected,
                            colors = ButtonDefaults.buttonColors(containerColor = AccentGreen),
                            shape = RoundedCornerShape(8.dp)
                        ) {
                            Icon(Icons.Default.Link, contentDescription = null)
                            Spacer(Modifier.width(4.dp))
                            Text("连接")
                        }

                        Button(
                            onClick = { viewModel.disconnect() },
                            modifier = Modifier.weight(1f),
                            enabled = uiState.isConnected,
                            colors = ButtonDefaults.buttonColors(containerColor = AccentRed),
                            shape = RoundedCornerShape(8.dp)
                        ) {
                            Icon(Icons.Default.LinkOff, contentDescription = null)
                            Spacer(Modifier.width(4.dp))
                            Text("断开")
                        }
                    }

                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.spacedBy(8.dp)
                    ) {
                        StatusBadge(
                            label = "连接状态",
                            value = if (uiState.isConnected) "已连接" else "未连接",
                            color = if (uiState.isConnected) AccentGreen else TextSecondary
                        )
                    }
                }
            }
        }
    }
}
