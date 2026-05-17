package com.audiochat.phone.ui

import androidx.compose.foundation.layout.*
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp

@Composable
fun DebugScreen(viewModel: MainViewModel, uiState: PhoneUiState) {
    var selectedDebugTab by remember { mutableIntStateOf(0) }
    
    val debugTabs = listOf(
        "连接" to Icons.Default.Link,
        "事件" to Icons.Default.List,
        "WS调试" to Icons.Default.Code,
        "设备" to Icons.Default.Devices,
        "视频" to Icons.Default.Videocam,
        "相机" to Icons.Default.CameraAlt,
        "日志" to Icons.Default.Terminal
    )

    Column(modifier = Modifier.fillMaxSize()) {
        ScrollableTabRow(
            selectedTabIndex = selectedDebugTab,
            containerColor = DarkSurface,
            contentColor = TextPrimary,
            edgePadding = 8.dp
        ) {
            debugTabs.forEachIndexed { index, (title, icon) ->
                Tab(
                    selected = selectedDebugTab == index,
                    onClick = { selectedDebugTab = index },
                    text = { 
                        Row(verticalAlignment = androidx.compose.ui.Alignment.CenterVertically) {
                            Icon(icon, contentDescription = null, modifier = Modifier.size(16.dp))
                            Spacer(Modifier.width(4.dp))
                            Text(title, fontSize = 12.sp)
                        }
                    },
                    selectedContentColor = AccentBlue,
                    unselectedContentColor = TextSecondary
                )
            }
        }

        Box(modifier = Modifier.fillMaxSize()) {
            when (selectedDebugTab) {
                0 -> ConnectionScreen(viewModel = viewModel, uiState = uiState)
                1 -> EventLogScreen(viewModel = viewModel, uiState = uiState)
                2 -> WebSocketDebugScreen(viewModel = viewModel, uiState = uiState)
                3 -> DeviceDebugScreen(viewModel = viewModel, uiState = uiState)
                4 -> VideoDisplayScreen(
                    currentFrame = uiState.currentFrame,
                    taskState = uiState.peerVideoTaskState,
                    detections = uiState.currentDetections,
                    onStopTask = { viewModel.stopPeerVideoTask() }
                )
                5 -> CameraDebugScreen(viewModel = viewModel, uiState = uiState)
                6 -> LogScreen(viewModel = viewModel, uiState = uiState)
            }
        }
    }
}
