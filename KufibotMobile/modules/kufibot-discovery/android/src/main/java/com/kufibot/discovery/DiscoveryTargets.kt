package com.kufibot.discovery

import java.net.Inet4Address
import java.net.InetAddress

/** A bounded sweep of the actual Wi-Fi subnet, including masks other than /24. */
internal object DiscoveryTargets {
  fun unicast(local: Inet4Address, prefix: Int, round: Long): List<InetAddress> {
    if (prefix !in 1..30) return emptyList()
    val address = local.address.fold(0L) { n, byte -> (n shl 8) or (byte.toLong() and 255) }
    val size = 1L shl (32 - prefix)
    val network = address and (0xffffffffL xor (size - 1))
    val chunks = (size + 255) / 256
    val chunk = (((address - network) / 256 + round % chunks) % chunks) * 256
    return (chunk until minOf(chunk + 256, size)).mapNotNull { offset ->
      val host = network + offset
      if (offset == 0L || offset == size - 1 || host == address) null
      else InetAddress.getByAddress(ByteArray(4) { index -> (host ushr (24 - index * 8)).toByte() })
    }
  }
}
