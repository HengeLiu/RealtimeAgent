package com.audiochat.phone.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun EventLogScreen(viewModel: MainViewModel, uiState: PhoneUiState) {
    Column(
        modifier = Modifier
            .fillMaxSize()
            .padding(12.dp),
        verticalArrangement = Arrangement.spacedBy(8.dp)
    ) {
        // 事件统计
        DebugCard {
            SectionTitle("事件统计")
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceEvenly
            ) {
                StatItem("总事件", "${uiState.eventsReceived}")
                StatItem("控制事件", "${uiState.controlEventsCount}")
                StatItem("流事件", "${uiState.streamEventsCount}")
                StatItem("命令事件", "${uiState.commandEventsCount}")
            }
        }

        // 事件过滤
        DebugCard {
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.spacedBy(8.dp),
                verticalAlignment = Alignment.CenterVertically
            ) {
                Text("过滤:", color = TextSecondary, fontSize = 12.sp)

                FilterChip(
                    selected = uiState.eventFilter == "all",
                    onClick = { viewModel.setEventFilter("all") },
                    label = { Text("全部", fontSize = 11.sp) },
                    colors = FilterChipDefaults.filterChipColors(
                        selectedContainerColor = AccentBlue,
                        selectedLabelColor = Color.White
                    )
                )
                FilterChip(
                    selected = uiState.eventFilter == "control",
                    onClick = { viewModel.setEventFilter("control") },
                    label = { Text("控制", fontSize = 11.sp) },
                    colors = FilterChipDefaults.filterChipColors(
                        selectedContainerColor = AccentGreen,
                        selectedLabelColor = Color.White
                    )
                )
                FilterChip(
                    selected = uiState.eventFilter == "stream",
                    onClick = { viewModel.setEventFilter("stream") },
                    label = { Text("流", fontSize = 11.sp) },
                    colors = FilterChipDefaults.filterChipColors(
                        selectedContainerColor = AccentYellow,
                        selectedLabelColor = Color.White
                    )
                )
                FilterChip(
                    selected = uiState.eventFilter == "command",
                    onClick = { viewModel.setEventFilter("command") },
                    label = { Text("命令", fontSize = 11.sp) },
                    colors = FilterChipDefaults.filterChipColors(
                        selectedContainerColor = AccentRed,
                        selectedLabelColor = Color.White
                    )
                )
            }

            Spacer(Modifier.height(8.dp))

            OutlinedButton(
                onClick = viewModel::clearEvents,
                colors = ButtonDefaults.outlinedButtonColors(contentColor = AccentRed),
                modifier = Modifier.fillMaxWidth()
            ) {
                Icon(Icons.Default.Delete, null, Modifier.size(16.dp))
                Spacer(Modifier.width(4.dp))
                Text("清空事件", fontSize = 12.sp)
            }
        }

        // 事件列表
        DebugCard(modifier = Modifier.weight(1f)) {
            SectionTitle("事件列表 (${uiState.filteredEvents.size})")
            Spacer(Modifier.height(4.dp))

            if (uiState.filteredEvents.isEmpty()) {
                Box(
                    modifier = Modifier.fillMaxWidth().padding(24.dp),
                    contentAlignment = Alignment.Center
                ) {
                    Text("暂无事件", color = TextSecondary, fontSize = 14.sp)
                }
            } else {
                LazyColumn(
                    verticalArrangement = Arrangement.spacedBy(4.dp)
                ) {
                    items(uiState.filteredEvents) { event ->
                        EventItem(event)
                    }
                }
            }
        }
    }
}

@Composable
fun EventItem(event: EventLogEntry) {
    val eventColor = when {
        event.eventName.startsWith("control.") -> AccentBlue
        event.eventName.startsWith("stream.") -> AccentGreen
        event.eventName.startsWith("command.") -> AccentYellow
        event.eventName.contains("error") || event.eventName.contains("failed") -> AccentRed
        else -> TextSecondary
    }

    val directionIcon = when (event.direction) {
        "send" -> Icons.Default.ArrowUpward
        "recv" -> Icons.Default.ArrowDownward
        else -> Icons.Default.ArrowForward
    }

    val directionLabel = when (event.direction) {
        "send" -> "发送"
        "recv" -> "接收"
        else -> ""
    }

    val directionColor = when (event.direction) {
        "send" -> AccentGreen
        "recv" -> AccentBlue
        else -> TextSecondary
    }

    Column(
        modifier = Modifier
            .fillMaxWidth()
            .background(BorderColor.copy(alpha = 0.3f), RoundedCornerShape(6.dp))
            .padding(8.dp)
    ) {
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically
        ) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Icon(
                    directionIcon,
                    null,
                    tint = directionColor,
                    modifier = Modifier.size(14.dp)
                )
                Spacer(Modifier.width(4.dp))
                Text(
                    directionLabel,
                    color = directionColor,
                    fontSize = 10.sp,
                    fontWeight = FontWeight.Bold
                )
                Spacer(Modifier.width(6.dp))
                Text(
                    event.timestamp,
                    color = TextSecondary,
                    fontSize = 10.sp,
                    fontFamily = androidx.compose.ui.text.font.FontFamily.Monospace
                )
            }
            Text(
                event.eventName,
                color = eventColor,
                fontSize = 11.sp,
                fontWeight = FontWeight.Medium,
                fontFamily = androidx.compose.ui.text.font.FontFamily.Monospace
            )
        }

        if (event.detail.isNotEmpty()) {
            Spacer(Modifier.height(4.dp))
            Text(
                event.detail,
                color = TextSecondary,
                fontSize = 10.sp,
                fontFamily = androidx.compose.ui.text.font.FontFamily.Monospace,
                maxLines = 3
            )
        }
    }
}