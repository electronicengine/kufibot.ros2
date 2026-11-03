package com.kufibot.discovery

import android.content.Context
import android.net.ConnectivityManager
import android.net.NetworkCapabilities
import expo.modules.kotlin.modules.Module
import expo.modules.kotlin.modules.ModuleDefinition
import java.net.HttpURLConnection
import java.util.concurrent.Callable
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit
import java.net.DatagramPacket
import java.net.DatagramSocket
import java.net.Inet4Address
import java.net.InetAddress
import java.net.SocketTimeoutException
import java.util.concurrent.atomic.AtomicReference
import org.json.JSONObject

class KufibotDiscoveryModule : Module() {
  private val active = AtomicReference<DatagramSocket?>(null)
  private var scanRound = 0L
  private var lastNetwork: String? = null
  private val knownHosts = linkedSetOf<InetAddress>()
  private var connectivity: ConnectivityManager? = null

  override fun definition() = ModuleDefinition {
    Name("KufibotDiscovery")
    // Expo AsyncFunction runs on its background queue, never Android's UI thread.
    AsyncFunction("scan") { scan() }
    OnDestroy {
      active.getAndSet(null)?.close()
      connectivity?.bindProcessToNetwork(null)
    }
  }

  private fun scan(): List<Map<String, Any>> {
    val found = linkedMapOf<String, Map<String, Any>>()
    val context = appContext.reactContext ?: error("Uygulama bağlamı hazır değil")
    val manager = context.getSystemService(Context.CONNECTIVITY_SERVICE) as ConnectivityManager
    connectivity = manager
    // A setup hotspot has no internet. Do not let Android route local control
    // through cellular, or filter this Wi-Fi out for lacking VALIDATED/INTERNET.
    val candidates = manager.allNetworks.filter { candidate ->
      val capabilities = manager.getNetworkCapabilities(candidate)
      capabilities?.hasTransport(NetworkCapabilities.TRANSPORT_WIFI) == true &&
        !capabilities.hasTransport(NetworkCapabilities.TRANSPORT_VPN) &&
        manager.getLinkProperties(candidate)?.linkAddresses?.any { it.address is Inet4Address } == true
    }
    // Prefer the current network. A Wi-Fi Network can disappear between the
    // snapshot above and binding (especially during setup -> home Wi-Fi).
    // Try the remaining candidates instead of throwing IllegalStateException.
    val network = candidates.sortedBy { it != manager.activeNetwork }
      .firstOrNull { manager.bindProcessToNetwork(it) }
    if (network == null) {
      manager.bindProcessToNetwork(null)
      throw java.io.IOException(if (candidates.isEmpty())
        "Telefonun kullanılabilir bir Wi-Fi bağlantısı yok. Robotla aynı Wi-Fi ağına bağlanın."
        else "Telefon Wi-Fi ağına erişemiyor. VPN veya yerel ağ erişim ayarlarını kontrol edin.")
    }
    if (lastNetwork != network.toString()) {
      lastNetwork = network.toString()
      scanRound = 0L
      knownHosts.clear()
    }
    val links = manager.getLinkProperties(network)
    DatagramSocket().use { socket ->
      active.getAndSet(socket)?.close()
      try {
        network.bindSocket(socket)
        socket.broadcast = true
        socket.soTimeout = 200
        val targets = mutableSetOf(InetAddress.getByName("255.255.255.255"))
        links?.linkAddresses?.forEach { link ->
          if (link.address is Inet4Address && link.prefixLength in 1..30) {
            val bytes = link.address.address
            val address = bytes.fold(0) { value, byte -> (value shl 8) or (byte.toInt() and 255) }
            val broadcast = address or (-1 ushr link.prefixLength)
            targets.add(InetAddress.getByAddress(ByteArray(4) { index ->
              (broadcast ushr (24 - index * 8)).toByte()
            }))
          }
        }
        // Some routers suppress broadcast while allowing ordinary local UDP.
        // Probe at most 256 hosts per subnet per scan; larger networks rotate
        // through batches. Known responders are retried on every scan.
        targets.addAll(knownHosts)
        links?.routes?.mapNotNull { it.gateway as? Inet4Address }
          ?.filter { !it.isAnyLocalAddress && !it.isLoopbackAddress }?.forEach { targets.add(it) }
        links?.linkAddresses?.take(2)?.forEach { link ->
          val address = link.address
          if (address is Inet4Address) targets.addAll(DiscoveryTargets.unicast(address, link.prefixLength, scanRound))
        }
        scanRound++
        var sent = false
        targets.forEach { target ->
          // V2 belongs to the network watchdog; V1 supports older robots.
          listOf("KUFIBOT_DISCOVER_V2" to 8889, "KUFIBOT_DISCOVER_V1" to 8888).forEach { (text, port) ->
            val probe = text.toByteArray(Charsets.UTF_8)
            try {
              socket.send(DatagramPacket(probe, probe.size, target, port))
              sent = true
            } catch (_: java.io.IOException) { /* Another target may work. */ }
          }
        }
        check(sent) { "Wi-Fi ağına keşif paketi gönderilemedi" }
        val deadline = System.nanoTime() + 2_000_000_000L
        while (!socket.isClosed && System.nanoTime() < deadline) {
          val packet = DatagramPacket(ByteArray(1024), 1024)
          try {
            socket.receive(packet)
          } catch (_: SocketTimeoutException) { continue }
          try {
            val data = JSONObject(String(packet.data, 0, packet.length, Charsets.UTF_8))
            val port = data.optInt("port", 0)
            val host = packet.address.hostAddress ?: continue
            val version = data.optInt("version")
            if (packet.address is Inet4Address && !packet.address.isLoopbackAddress &&
                !packet.address.isMulticastAddress && data.optString("service") == "kufibot" &&
                version in 1..2 && port in 1..65535) {
              val key = "$host:$port"
              if (version == 1 && found[key]?.containsKey("robotId") == true) continue
              val robot = mutableMapOf<String, Any>("host" to host, "port" to port,
                "name" to data.optString("name", "Kufibot").take(60))
              val id = data.optString("robot_id")
              if (version == 2 && id.isNotEmpty() && id.length <= 128) {
                robot["robotId"] = id
                robot["stationConnected"] = data.optBoolean("station_connected")
                robot["setupActive"] = data.optBoolean("setup_active")
              }
              found[key] = robot
              if (knownHosts.size < 16) knownHosts.add(packet.address)
            }
          } catch (_: org.json.JSONException) { /* Ignore unrelated UDP packets. */ }
        }
      } finally {
        active.compareAndSet(socket, null)
      }
    }
    if (found.isEmpty()) {
      val http = HttpDiscovery { url -> network.openConnection(url) as HttpURLConnection }
      for (host in knownHosts.toList()) {
        http.probe(host.hostAddress ?: continue)?.let { found["${it["host"]}:${it["port"]}"] = it }
      }
      if (found.isEmpty()) {
        val hosts = linkedSetOf<InetAddress>()
        links?.routes?.mapNotNull { it.gateway as? Inet4Address }
          ?.filter { !it.isAnyLocalAddress && !it.isLoopbackAddress }?.forEach { hosts.add(it) }
        links?.linkAddresses?.take(2)?.forEach { link ->
          val address = link.address
          if (address is Inet4Address) hosts.addAll(DiscoveryTargets.unicast(address, link.prefixLength, scanRound - 1))
        }
        val pool = Executors.newFixedThreadPool(16)
        try {
          val tasks = hosts.take(256).map { address -> Callable {
            http.probe(address.hostAddress ?: return@Callable null)
          } }
          for (future in pool.invokeAll(tasks, 8, TimeUnit.SECONDS)) {
            if (!future.isCancelled) {
              val robot = future.get() ?: continue
              found["${robot["host"]}:${robot["port"]}"] = robot
              if (knownHosts.size < 16) knownHosts.add(InetAddress.getByName(robot["host"] as String))
            }
          }
        } finally { pool.shutdownNow() }
      }
    }
    return found.values.toList()
  }
}
