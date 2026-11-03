package com.kufibot.discovery

import java.net.HttpURLConnection
import java.net.URL
import org.json.JSONObject

/** Read-only fallback for networks where UDP discovery is filtered or unavailable. */
internal class HttpDiscovery(private val open: (URL) -> HttpURLConnection) {
  private fun json(host: String, port: Int, path: String): JSONObject {
    val connection = open(URL("http://$host:$port/$path"))
    try {
      connection.connectTimeout = 300
      connection.readTimeout = 700
      connection.instanceFollowRedirects = false
      connection.useCaches = false
      if (connection.responseCode != 200) throw java.io.IOException("HTTP discovery unavailable")
      val bytes = connection.inputStream.use { stream ->
        val buffer = java.io.ByteArrayOutputStream()
        val chunk = ByteArray(4096)
        while (buffer.size() <= 32768) {
          val count = stream.read(chunk, 0, minOf(chunk.size, 32769 - buffer.size()))
          if (count < 0) break
          buffer.write(chunk, 0, count)
        }
        buffer.toByteArray()
      }
      if (bytes.size > 32768) throw java.io.IOException("Discovery response too large")
      return JSONObject(String(bytes, Charsets.UTF_8))
    } finally { connection.disconnect() }
  }

  fun probe(host: String, port: Int = 8080): Map<String, Any>? {
    try {
      val manifest = json(host, port, "manifest.webmanifest")
      if (manifest.optString("short_name") != "Kufibot" ||
          manifest.optString("name") != "Kufibot Kontrol Merkezi") return null
      val robot = mutableMapOf<String, Any>("host" to host, "port" to port, "name" to "Kufibot")
      // Older controllers need only the branded manifest. New controllers also
      // expose identity/state needed to follow a reset or a Wi-Fi handoff.
      try {
        val data = json(host, port, "api/device-settings/network")
        val setup = data.optJSONObject("hotspot")
        val id = setup?.optString("robot_id") ?: ""
        if (id.isNotEmpty() && id.length <= 128) {
          robot["robotId"] = id
          robot["stationConnected"] = data.optBoolean("station_connected")
          robot["setupActive"] = setup!!.optBoolean("active")
        }
      } catch (_: Exception) { /* The web UI is reachable even without network management. */ }
      return robot
    } catch (_: Exception) { return null }
  }
}
