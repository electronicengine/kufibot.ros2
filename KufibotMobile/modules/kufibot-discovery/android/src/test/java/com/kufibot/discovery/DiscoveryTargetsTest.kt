package com.kufibot.discovery

import java.net.Inet4Address
import java.net.InetAddress
import org.junit.Assert.*
import org.junit.Test

class DiscoveryTargetsTest {
  private fun hosts(ip: String, prefix: Int, round: Long = 0) =
    DiscoveryTargets.unicast(InetAddress.getByName(ip) as Inet4Address, prefix, round).map { it.hostAddress }.toSet()

  @Test fun setupSubnetIncludesGatewayWithoutSelfOrBroadcast() {
    val targets = hosts("10.42.0.12", 24)
    assertTrue(targets.contains("10.42.0.1"))
    assertEquals(253, targets.size)
    assertFalse(targets.contains("10.42.0.12"))
    assertFalse(targets.contains("10.42.0.0"))
    assertFalse(targets.contains("10.42.0.255"))
  }

  @Test fun smallSubnetNeverProbesNeighboursOutsideTheMask() {
    assertEquals(setOf("192.168.2.130"), hosts("192.168.2.129", 30))
    assertTrue(hosts("192.168.2.129", 32).isEmpty())
  }

  @Test fun widerSubnetRotatesAndIncludesDotZeroAndDot255Hosts() {
    val first = hosts("192.168.1.12", 23)
    val second = hosts("192.168.1.12", 23, 1)
    assertTrue(first.contains("192.168.1.0"))
    assertTrue(second.contains("192.168.0.255"))
    assertEquals(509, (first + second).size)
    assertEquals(first, hosts("192.168.1.12", 23, 2))
    assertTrue(first.size <= 256 && second.size <= 256)
  }
}
