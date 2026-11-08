package com.veksha.smsgateway

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.provider.Settings
import android.view.View
import android.widget.Toast
import androidx.activity.result.contract.ActivityResultContracts
import androidx.lifecycle.ViewModelProvider
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import com.google.android.material.snackbar.Snackbar
import com.google.firebase.messaging.FirebaseMessaging
import com.veksha.smsgateway.data.AppPreferences
import com.veksha.smsgateway.databinding.ActivityMainBinding
import com.veksha.smsgateway.ui.MainViewModel

class MainActivity : AppCompatActivity() {
    private lateinit var binding: ActivityMainBinding
    private lateinit var viewModel: MainViewModel
    private lateinit var prefs: AppPreferences

    private val permissionLauncher = registerForActivityResult(ActivityResultContracts.RequestMultiplePermissions()) { results ->
        updatePermissionStatus()
        val denied = results.filter { !it.value }.keys
        if (denied.isNotEmpty()) {
            Snackbar.make(binding.root, "SMS permission required to send alerts", Snackbar.LENGTH_LONG)
                .setAction("Settings") { openAppSettings() }.show()
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        viewModel = ViewModelProvider(this)[MainViewModel::class.java]
        binding = ActivityMainBinding.inflate(layoutInflater)
        setContentView(binding.root)
        prefs = AppPreferences(this)
        setupUI()
        observeViewModel()
        fetchFcmToken()
        updatePermissionStatus()
    }

    override fun onResume() {
        super.onResume()
        updatePermissionStatus()
        viewModel.refreshSmsLog()
        binding.tvRegistrationStatus.text = if (prefs.gatewayRegistered) "✅ Registered as SMS Gateway" else "❌ Not registered — tap Register"
    }

    private fun setupUI() {
        binding.etBackendUrl.setText(prefs.backendUrl)
        binding.etApiKey.setText(prefs.gatewayApiKey)
        binding.etContacts.setText(prefs.emergencyContacts)
        binding.switchAutoSms.isChecked = prefs.autoSmsEnabled
        binding.switchAutoSms.setOnCheckedChangeListener { _, v -> viewModel.setAutoSmsEnabled(v) }
        binding.btnSaveSettings.setOnClickListener {
            viewModel.setBackendUrl(binding.etBackendUrl.text.toString().trim())
            viewModel.setApiKey(binding.etApiKey.text.toString().trim())
            viewModel.setContacts(binding.etContacts.text.toString().trim())
            Toast.makeText(this, "Settings saved", Toast.LENGTH_SHORT).show()
        }
        binding.btnRequestPermissions.setOnClickListener { requestRequiredPermissions() }
        binding.btnRegister.setOnClickListener {
            viewModel.setBackendUrl(binding.etBackendUrl.text.toString().trim())
            viewModel.setApiKey(binding.etApiKey.text.toString().trim())
            viewModel.registerGateway()
        }
        binding.btnUnregister.setOnClickListener { viewModel.unregisterGateway() }
        binding.btnStartService.setOnClickListener {
            startForegroundService(Intent(this, GatewayForegroundService::class.java).apply { action = GatewayForegroundService.ACTION_START })
            Toast.makeText(this, "Gateway service started", Toast.LENGTH_SHORT).show()
        }
        binding.btnStopService.setOnClickListener {
            startService(Intent(this, GatewayForegroundService::class.java).apply { action = GatewayForegroundService.ACTION_STOP })
        }
        binding.btnClearLog.setOnClickListener { viewModel.clearLog() }
        binding.btnBatteryOptimization.setOnClickListener { requestBatteryOptimizationExclusion() }
    }

    private fun observeViewModel() {
        viewModel.registrationStatus.observe(this) { binding.tvRegistrationStatus.text = it }
        viewModel.smsLog.observe(this) { log ->
            binding.tvSmsLog.text = if (log.isEmpty()) "No SMS sent yet" else log.take(20).joinToString("\n")
        }
        viewModel.isLoading.observe(this) { loading ->
            binding.btnRegister.isEnabled = !loading
            binding.btnUnregister.isEnabled = !loading
            binding.progressBar.visibility = if (loading) View.VISIBLE else View.GONE
        }
    }

    private fun fetchFcmToken() {
        FirebaseMessaging.getInstance().token.addOnCompleteListener { task ->
            if (task.isSuccessful) {
                prefs.fcmToken = task.result
                binding.tvFcmToken.text = "FCM: ...${task.result.takeLast(10)}"
            } else {
                binding.tvFcmToken.text = "FCM token unavailable"
            }
        }
    }

    private fun updatePermissionStatus() {
        val smsPerm = ContextCompat.checkSelfPermission(this, Manifest.permission.SEND_SMS) == PackageManager.PERMISSION_GRANTED
        val notifPerm = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU)
            ContextCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS) == PackageManager.PERMISSION_GRANTED
        else true
        binding.tvPermissionStatus.text = "SMS: ${if (smsPerm) "✅" else "❌ Required"} | Notifications: ${if (notifPerm) "✅" else "❌ Required"}"
        binding.btnRequestPermissions.visibility = if (!smsPerm || !notifPerm) View.VISIBLE else View.GONE
    }

    private fun requestRequiredPermissions() {
        val perms = mutableListOf(Manifest.permission.SEND_SMS)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) perms.add(Manifest.permission.POST_NOTIFICATIONS)
        permissionLauncher.launch(perms.toTypedArray())
    }

    private fun requestBatteryOptimizationExclusion() {
        AlertDialog.Builder(this)
            .setTitle("Battery Optimization")
            .setMessage("Disable battery optimization for reliable alert delivery. Tap OK to open settings.")
            .setPositiveButton("Open Settings") { _, _ ->
                try { startActivity(Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS).apply { data = Uri.parse("package:$packageName") }) }
                catch (e: Exception) { startActivity(Intent(Settings.ACTION_IGNORE_BATTERY_OPTIMIZATION_SETTINGS)) }
            }.setNegativeButton("Cancel", null).show()
    }

    private fun openAppSettings() {
        startActivity(Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS).apply { data = Uri.parse("package:$packageName") })
    }
}

