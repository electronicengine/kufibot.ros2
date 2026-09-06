package com.kufibot.discovery

import expo.modules.kotlin.modules.Module
import expo.modules.kotlin.modules.ModuleDefinition
import java.net.DatagramPacket
import java.net.DatagramSocket
import java.net.Inet4Address
import java.net.InetAddress
import java.net.NetworkInterface
import java.net.SocketTimeoutException
import java.util.concurrent.atomic.AtomicReference
import org.json.JSONObject

class KufibotDiscoveryModule : Module() {
  private val active = AtomicReference<DatagramSocket?>(null)

  override fun definition() = ModuleDefinition {
    Name("KufibotDiscovery")
    // Expo AsyncFunction runs on its background queue, never Android's UI thread.
    AsyncFunction("scan") { scan() }
    OnDestroy { active.getAndSet(null)?.close() }
  }

  private fun scan(): List<Map<String, Any>> {
    val found = linkedMapOf<String, Map<String, Any>>()
    DatagramSocket().use { socket ->
      active.getAndSet(socket)?.close()
      try {
        socket.broadcast = true
        socket.soTimeout = 200
        val targets = mutableSetOf(InetAddress.getByName("255.255.255.255"))
        NetworkInterface.getNetworkInterfaces()?.toList()?.forEach { network ->
          if (network.isUp && !network.isLoopback) {
            network.interfaceAddresses.forEach { address ->
              address.broadcast?.let { targets.add(it) }
            }
          }
        }
        val probe = "KUFIBOT_DISCOVER_V1".toByteArray(Charsets.UTF_8)
        var sent = false
        targets.forEach { target ->
          try {
            socket.send(DatagramPacket(probe, probe.size, target, 8888))
            sent = true
          } catch (_: java.io.IOException) { /* Another interface may work. */ }
        }
        check(sent) { "Wi-Fi ağına keşif paketi gönderilemedi" }
        val deadline = System.nanoTime() + 1_000_000_000L
        while (!socket.isClosed && System.nanoTime() < deadline) {
          val packet = DatagramPacket(ByteArray(1024), 1024)
          try {
            socket.receive(packet)
          } catch (_: SocketTimeoutException) { continue }
          try {
            val data = JSONObject(String(packet.data, 0, packet.length, Charsets.UTF_8))
            val port = data.optInt("port", 0)
            val host = packet.address.hostAddress ?: continue
            if (packet.address is Inet4Address && data.optString("service") == "kufibot" &&
                data.optInt("version") == 1 && port in 1..65535) {
              found["$host:$port"] = mapOf("host" to host, "port" to port,
                "name" to data.optString("name", "Kufibot").take(60))
            }
          } catch (_: org.json.JSONException) { /* Ignore unrelated UDP packets. */ }
        }
      } finally {
        active.compareAndSet(socket, null)
      }
    }
    return found.values.toList()
  }
}
