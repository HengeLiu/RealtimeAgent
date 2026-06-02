package com.audiochat.phone.ui

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.*
import com.audiochat.phone.ble.BleProvisioner
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.viewmodel.compose.viewModel

internal val DarkBackground = Color(0xFF0F1722)
internal val DarkSurface = Color(0xFF1E293B)
internal val DarkCard = Color(0xFF1E293B)
internal val AccentGreen = Color(0xFF22C55E)
internal val AccentBlue = Color(0xFF3B82F6)
internal val AccentRed = Color(0xFFEF4444)
internal val AccentYellow = Color(0xFFEAB308)
internal val TextPrimary = Color(0xFFF1F5F9)
internal val TextSecondary = Color(0xFF94A3B8)
internal val BorderColor = Color(0xFF334155)

data class MainTab(val title: String, val icon: ImageVector)

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()

        // 保持屏幕常亮
        window.addFlags(android.view.WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)

        setContent {
            MaterialTheme(
                colorScheme = darkColorScheme(
                    primary = AccentBlue,
                    secondary = AccentGreen,
                    background = DarkBackground,
                    surface = DarkSurface,
                    error = AccentRed
                )
            ) {
                Surface(modifier = Modifier.fillMaxSize(), color = DarkBackground) {
                    val viewModel: MainViewModel = viewModel()
                    MainApp(viewModel = viewModel)
                }
            }
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun MainApp(viewModel: MainViewModel) {
    val uiState by viewModel.uiState.collectAsState()

    val tabs = listOf(
        MainTab("用户", Icons.Default.Person),
        MainTab("配网", Icons.Default.Bluetooth),
        MainTab("调试", Icons.Default.BugReport)
    )

    var selectedTab by remember { mutableIntStateOf(0) }

    Scaffold(
        topBar = {
            TopAppBar(
                title = {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Text("AudioChat", fontWeight = FontWeight.Bold)
                        Spacer(Modifier.width(12.dp))
                        ConnectionDot(isConnected = uiState.isConnected, isRegistered = uiState.isRegistered)
                    }
                },
                colors = TopAppBarDefaults.topAppBarColors(
                    containerColor = DarkSurface,
                    titleContentColor = TextPrimary
                ),
                actions = {
                    Text(
                        text = if (uiState.isRegistered) "已注册" else if (uiState.isConnected) "连接中" else "未连接",
                        color = if (uiState.isRegistered) AccentGreen else TextSecondary,
                        fontSize = 12.sp,
                        modifier = Modifier.padding(end = 12.dp)
                    )
                }
            )
        },
        bottomBar = {
            NavigationBar(
                containerColor = DarkSurface,
                contentColor = TextPrimary
            ) {
                tabs.forEachIndexed { index, tab ->
                    NavigationBarItem(
                        icon = { Icon(tab.icon, contentDescription = tab.title) },
                        label = { Text(tab.title, fontSize = 12.sp) },
                        selected = selectedTab == index,
                        onClick = { selectedTab = index },
                        colors = NavigationBarItemDefaults.colors(
                            selectedIconColor = AccentBlue,
                            selectedTextColor = AccentBlue,
                            unselectedIconColor = TextSecondary,
                            unselectedTextColor = TextSecondary,
                            indicatorColor = DarkCard
                        )
                    )
                }
            }
        }
    ) { paddingValues ->
        Box(modifier = Modifier.padding(paddingValues)) {
            when (selectedTab) {
                0 -> UserScreen(viewModel = viewModel, uiState = uiState)
                1 -> {
                    val context = androidx.compose.ui.platform.LocalContext.current
                    val bleProvisioner = remember { BleProvisioner(context) }
                    ProvisionScreen(
                        bleProvisioner = bleProvisioner,
                        onDone = { selectedTab = 0 }
                    )
                }
                2 -> DebugScreen(viewModel = viewModel, uiState = uiState)
            }
        }
    }
}

@Composable
fun ConnectionDot(isConnected: Boolean, isRegistered: Boolean) {
    Box(
        modifier = Modifier
            .size(10.dp)
            .background(
                color = when {
                    isRegistered -> AccentGreen
                    isConnected -> AccentYellow
                    else -> AccentRed
                },
                shape = RoundedCornerShape(5.dp)
            )
    )
}

@Composable
fun SectionTitle(title: String, fontSize: androidx.compose.ui.unit.TextUnit = 14.sp) {
    Text(
        text = title,
        color = AccentBlue,
        fontSize = fontSize,
        fontWeight = FontWeight.Bold,
        modifier = Modifier.padding(bottom = 8.dp)
    )
}

@Composable
fun DebugCard(
    modifier: Modifier = Modifier,
    content: @Composable ColumnScope.() -> Unit
) {
    Card(
        modifier = modifier.fillMaxWidth(),
        shape = RoundedCornerShape(10.dp),
        colors = CardDefaults.cardColors(containerColor = DarkCard),
        border = androidx.compose.foundation.BorderStroke(1.dp, BorderColor)
    ) {
        Column(modifier = Modifier.padding(14.dp), content = content)
    }
}

@Composable
fun StatusBadge(label: String, value: String, color: Color = TextPrimary) {
    Row(
        modifier = Modifier
            .background(BorderColor, RoundedCornerShape(4.dp))
            .padding(horizontal = 8.dp, vertical = 4.dp),
        verticalAlignment = Alignment.CenterVertically
    ) {
        Text("$label: ", color = TextSecondary, fontSize = 11.sp)
        Text(value, color = color, fontSize = 11.sp, fontWeight = FontWeight.Medium)
    }
}

@Composable
fun MonoText(text: String, color: Color = TextPrimary, fontSize: Int = 12) {
    Text(
        text = text,
        color = color,
        fontSize = fontSize.sp,
        fontFamily = androidx.compose.ui.text.font.FontFamily.Monospace
    )
}
