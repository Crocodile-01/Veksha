package com.veksha.smsgateway.ui

import android.app.Application
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.LiveData
import androidx.lifecycle.MutableLiveData
import androidx.lifecycle.viewModelScope
import com.veksha.smsgateway.GatewayRegistrationManager
import com.veksha.smsgateway.data.AppPreferences
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

class MainViewModel(application: Application) : AndroidViewModel(application) {
    private val prefs = AppPreferences(application)
    private val registrationManager = GatewayRegistrationManager(application)

    private val _registrationStatus = MutableLiveData<String>()
    val registrationStatus: LiveData<String> = _registrationStatus

    private val _smsLog = MutableLiveData<List<String>>()
    val smsLog: LiveData<List<String>> = _smsLog

    private val _isLoading = MutableLiveData(false)
    val isLoading: LiveData<Boolean> = _isLoading

    fun refreshSmsLog() { _smsLog.value = prefs.getSmsLog() }

    fun registerGateway() = viewModelScope.launch {
        _isLoading.value = true
        val result = withContext(Dispatchers.IO) { registrationManager.register() }
        _registrationStatus.value = result.message
        _isLoading.value = false
    }

    fun unregisterGateway() = viewModelScope.launch {
        _isLoading.value = true
        val result = withContext(Dispatchers.IO) { registrationManager.unregister() }
        _registrationStatus.value = result.message
        _isLoading.value = false
    }

    fun clearLog() { prefs.clearSmsLog(); _smsLog.value = emptyList() }

    fun isRegistered() = prefs.gatewayRegistered
    fun getAutoSmsEnabled() = prefs.autoSmsEnabled
    fun setAutoSmsEnabled(v: Boolean) { prefs.autoSmsEnabled = v }
    fun getBackendUrl() = prefs.backendUrl
    fun setBackendUrl(v: String) { prefs.backendUrl = v }
    fun getApiKey() = prefs.gatewayApiKey
    fun setApiKey(v: String) { prefs.gatewayApiKey = v }
    fun getContacts() = prefs.emergencyContacts
    fun setContacts(v: String) { prefs.emergencyContacts = v }
    fun getFcmToken() = prefs.fcmToken
    fun isGatewayRegistered() = prefs.gatewayRegistered
}

