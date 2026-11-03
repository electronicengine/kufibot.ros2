package com.kufibot.discovery

import java.net.ServerSocket
import java.net.InetAddress
import kotlin.concurrent.thread
import java.net.HttpURLConnection
import org.junit.Assert.*
import org.junit.Test

class HttpDiscoveryTest {
  private val manifest = """{"name":"Kufibot Kontrol Merkezi","short_name":"Kufibot"}"""

  private fun withServer(body: String, status: Int = 200, run: (HttpDiscovery, Int) -> Unit) {
    val server = ServerSocket(0, 16, InetAddress.getByName("127.0.0.1"))
    val worker = thread(isDaemon = true) {
      while (!server.isClosed) {
        try {
          server.accept().use { socket ->
            socket.soTimeout = 1000
            val reader = socket.getInputStream().bufferedReader()
            val request = reader.readLine() ?: ""
            while (!reader.readLine().isNullOrEmpty()) { }
            val network = request.contains("/api/device-settings/network ")
            val bytes = (if (network)
              """{"station_connected":true,"hotspot":{"robot_id":"robot-a","active":false}}"""
              else body).toByteArray()
            val code = if (network) 200 else status
            val output = socket.getOutputStream()
            output.write("HTTP/1.1 $code Test\r\nContent-Length: ${bytes.size}\r\nConnection: close\r\n\r\n".toByteArray())
            output.write(bytes)
            output.flush()
          }
        } catch (_: java.io.IOException) { }
      }
    }
    try { run(HttpDiscovery { it.openConnection() as HttpURLConnection }, server.localPort) }
    finally { server.close(); worker.join(2000) }
  }

  @Test fun discoversReachableControllerWithoutAnyUdpService() {
    withServer(manifest) { discovery, port ->
      val robot = discovery.probe("127.0.0.1", port)!!
      assertEquals("127.0.0.1", robot["host"])
      assertEquals(port, robot["port"])
      assertEquals("robot-a", robot["robotId"])
      assertEquals(true, robot["stationConnected"])
      assertEquals(false, robot["setupActive"])
    }
  }

  @Test fun rejectsOtherWebServersAndMalformedOrOversizedResponses() {
    for (body in listOf("{}", "<html>router</html>", " ".repeat(32769),
        """{"name":"Another application","short_name":"Kufibot"}""")) {
      withServer(body) { discovery, port -> assertNull(discovery.probe("127.0.0.1", port)) }
    }
  }

  @Test fun redirectDoesNotCountAsRobotDiscovery() {
    withServer(manifest, 302) { discovery, port -> assertNull(discovery.probe("127.0.0.1", port)) }
  }
}
