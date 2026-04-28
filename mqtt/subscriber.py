"""Meshtastic MQTT ingest pipeline.

Subscribes to one or more `msh/...` topics and processes every packet through:

    bytes -> ServiceEnvelope -> MeshPacket -> (decrypt if encrypted)
          -> Data (protobuf) -> portnum-specific handler -> DataStore

Encryption uses AES-CTR with the channel PSK; the nonce is the 8-byte little-
endian packet id concatenated with the 8-byte little-endian sender id, which
is the standard Meshtastic scheme. Decryption failure silently drops the
packet (it usually means the message was sent on a different channel/PSK).

Every packet — even ones we can't decrypt fully — produces a row in
`packet_observations` so the black-hole detector can reason about which nodes
are relaying which traffic.
"""

import base64
import logging
import time
import math

import paho.mqtt.client as mqtt
from meshtastic.protobuf import mqtt_pb2, mesh_pb2, portnums_pb2, telemetry_pb2
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from database.store import DataStore

log = logging.getLogger(__name__)


def haversine(lat1, lon1, lat2, lon2):
    """Great-circle distance between two lat/lon points (km)."""
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def node_id_to_hex(node_num: int) -> str:
    """Convert a 32-bit Meshtastic node number to its canonical `!xxxxxxxx` form."""
    return f"!{node_num:08x}"


class MQTTSubscriber:
    """Long-running MQTT client that decodes Meshtastic packets into the DataStore.

    Runs paho-mqtt in its own background loop (`loop_start`); auto-reconnects
    on disconnect. `default_key_b64` is the base64 channel PSK used to decrypt
    `MeshPacket.encrypted` payloads when present.
    """

    def __init__(self, store: DataStore, host: str, port: int, user: str, password: str,
                 topics: list[str], default_key_b64: str):
        self.store = store
        self.host = host
        self.port = port
        self.user = user
        self.password = password
        self.topics = topics
        self.default_key = base64.b64decode(default_key_b64)
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        self.client.username_pw_set(self.user, self.password)
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message
        self.client.on_disconnect = self._on_disconnect

    def start(self):
        log.info("Connecting to MQTT broker %s:%d", self.host, self.port)
        self.client.connect(self.host, self.port, 60)
        self.client.loop_start()

    def stop(self):
        self.client.loop_stop()
        self.client.disconnect()

    def _on_connect(self, client, userdata, flags, rc, properties=None):
        log.info("MQTT connected with rc=%s", rc)
        for topic in self.topics:
            client.subscribe(topic)
            log.info("Subscribed to %s", topic)

    def _on_disconnect(self, client, userdata, flags, rc, properties=None):
        log.warning("MQTT disconnected rc=%s, will auto-reconnect", rc)

    def _decrypt(self, mp):
        """AES-CTR decrypt a MeshPacket payload using the configured PSK.

        Nonce = packet_id (8 bytes LE) || sender_id (8 bytes LE). Returns the
        parsed `mesh_pb2.Data` on success, or None on any failure (wrong key,
        truncated payload, etc.) — callers must treat None as "drop packet".
        """
        try:
            nonce = mp.id.to_bytes(8, "little") + getattr(mp, "from").to_bytes(8, "little")
            cipher = Cipher(algorithms.AES(self.default_key), modes.CTR(nonce))
            decryptor = cipher.decryptor()
            decrypted = decryptor.update(mp.encrypted) + decryptor.finalize()
            data = mesh_pb2.Data()
            data.ParseFromString(decrypted)
            return data
        except Exception:
            return None

    def _on_message(self, client, userdata, msg):
        """paho-mqtt callback for every received MQTT message.

        Order matters: the node row is upserted (`last_seen`) and a packet
        observation row is recorded *before* dispatching to the portnum
        handler, so even unhandled portnums contribute to liveness/black-hole
        analysis.
        """
        try:
            env = mqtt_pb2.ServiceEnvelope()
            env.ParseFromString(msg.payload)
            mp = env.packet

            if mp.HasField("decoded"):
                data = mp.decoded
            elif mp.encrypted:
                data = self._decrypt(mp)
                if not data:
                    return
            else:
                return

            from_id = node_id_to_hex(getattr(mp, "from"))
            to_raw = mp.to
            to_id = node_id_to_hex(to_raw) if to_raw else None
            rx_time = int(time.time())

            # Update node last_seen on any packet
            self.store.upsert_node(from_id, last_seen=rx_time)

            # --- Capture packet envelope metadata for black hole detection ---
            hop_start = mp.hop_start if mp.hop_start > 0 else None
            hop_limit = mp.hop_limit if mp.hop_limit > 0 else None
            rx_snr = mp.rx_snr if mp.rx_snr != 0 else None
            rx_rssi = mp.rx_rssi if mp.rx_rssi != 0 else None
            via_mqtt = mp.via_mqtt
            relay_node_raw = mp.relay_node if mp.relay_node else None
            relay_node = node_id_to_hex(relay_node_raw) if relay_node_raw else None
            channel = mp.channel

            try:
                self.store.insert_packet_observation(
                    timestamp=rx_time,
                    packet_id=mp.id,
                    from_id=from_id,
                    to_id=to_id,
                    portnum=data.portnum,
                    hop_start=hop_start,
                    hop_limit=hop_limit,
                    rx_snr=rx_snr,
                    rx_rssi=rx_rssi,
                    via_mqtt=via_mqtt,
                    relay_node=relay_node,
                    channel=channel,
                )
            except Exception as e:
                log.debug("Failed to record packet observation: %s", e)

            portnum = data.portnum
            if portnum == portnums_pb2.PortNum.POSITION_APP:
                self._handle_position(from_id, data.payload, rx_time)
            elif portnum == portnums_pb2.PortNum.TELEMETRY_APP:
                self._handle_telemetry(from_id, data.payload, rx_time)
            elif portnum == portnums_pb2.PortNum.NODEINFO_APP:
                self._handle_nodeinfo(from_id, data.payload)
            elif portnum == portnums_pb2.PortNum.NEIGHBORINFO_APP:
                self._handle_neighborinfo(from_id, data.payload, rx_time)
            elif portnum == portnums_pb2.PortNum.TRACEROUTE_APP:
                self._handle_traceroute(from_id, to_id, data.payload, rx_time)
            else:
                log.debug("Unhandled portnum %s from %s", portnums_pb2.PortNum.Name(portnum), from_id)

        except Exception as e:
            log.error("Error processing MQTT message: %s", e, exc_info=True)

    def _handle_position(self, from_id, payload, rx_time):
        """POSITION_APP: GPS fix. Drops `(0, 0)` as a sentinel for 'no fix'."""
        pos = mesh_pb2.Position()
        pos.ParseFromString(payload)
        lat = pos.latitude_i * 1e-7
        lon = pos.longitude_i * 1e-7
        if lat == 0.0 and lon == 0.0:
            return
        alt = pos.altitude if pos.altitude else None
        ts = pos.time if pos.time else rx_time
        self.store.insert_position(from_id, ts, lat, lon, alt, pos.sats_in_view or None)
        log.debug("Position from %s: %.6f, %.6f alt=%s", from_id, lat, lon, alt)

    def _handle_telemetry(self, from_id, payload, rx_time):
        """TELEMETRY_APP: device metrics (battery, voltage, channel/air util, uptime)."""
        tel = telemetry_pb2.Telemetry()
        tel.ParseFromString(payload)
        if tel.HasField("device_metrics"):
            dm = tel.device_metrics
            self.store.insert_device_metrics(
                from_id, rx_time,
                battery=dm.battery_level if dm.battery_level else None,
                voltage=round(dm.voltage, 2) if dm.voltage else None,
                ch_util=round(dm.channel_utilization, 1) if dm.channel_utilization else None,
                air_util=round(dm.air_util_tx, 1) if dm.air_util_tx else None,
                uptime=dm.uptime_seconds if dm.uptime_seconds else None,
            )
            log.debug("Telemetry from %s: bat=%s%% v=%.2fV", from_id,
                       dm.battery_level, dm.voltage)

    def _handle_nodeinfo(self, from_id, payload):
        """NODEINFO_APP: human-readable identity (short/long name, hardware, role)."""
        user = mesh_pb2.User()
        user.ParseFromString(payload)
        hw = mesh_pb2.HardwareModel.Name(user.hw_model) if user.hw_model else None
        role = mesh_pb2.User().DESCRIPTOR.fields_by_name["role"].enum_type.values_by_number.get(user.role)
        role_name = role.name if role else None
        self.store.upsert_node(
            from_id,
            short_name=user.short_name or None,
            long_name=user.long_name or None,
            hw_model=hw,
            role=role_name,
        )
        log.debug("NodeInfo from %s: %s (%s) hw=%s role=%s", from_id, user.long_name, user.short_name, hw, role_name)

    def _handle_neighborinfo(self, from_id, payload, rx_time):
        """NEIGHBORINFO_APP: per-neighbor SNR snapshot.

        This is the primary source of link observations driving propagation
        analysis. Distance is filled in only when both endpoints have a known
        position; weather is associated by nearest timestamp via
        `get_weather_near_time` so correlation queries can join cheaply.
        Note: NEIGHBORINFO doesn't carry RSSI in older firmware — stored as NULL.
        """
        ni = mesh_pb2.NeighborInfo()
        ni.ParseFromString(payload)
        reporter_id = node_id_to_hex(ni.node_id) if ni.node_id else from_id
        a_pos = self.store.get_node_position(reporter_id)
        weather_id = self.store.get_weather_near_time(rx_time)

        for neighbor in ni.neighbors:
            neighbor_id = node_id_to_hex(neighbor.node_id)
            b_pos = self.store.get_node_position(neighbor_id)

            a_lat, a_lon = a_pos if a_pos else (None, None)
            b_lat, b_lon = b_pos if b_pos else (None, None)

            distance = None
            if a_lat and a_lon and b_lat and b_lon:
                distance = round(haversine(a_lat, a_lon, b_lat, b_lon), 2)

            self.store.insert_link_observation(
                rx_time, reporter_id, neighbor_id,
                a_lat=a_lat, a_lon=a_lon,
                b_lat=b_lat, b_lon=b_lon,
                snr=neighbor.snr if neighbor.snr else None,
                rssi=None,  # NeighborInfo doesn't include RSSI in all firmware versions
                distance=distance,
                weather_id=weather_id,
            )

            # Ensure neighbor node exists
            self.store.upsert_node(neighbor_id, last_seen=rx_time)

        log.debug("NeighborInfo from %s: %d neighbors", reporter_id, len(ni.neighbors))

    def _handle_traceroute(self, from_id, to_id, payload, rx_time):
        """Handle TRACEROUTE_APP responses — full hop-by-hop route data."""
        try:
            rd = mesh_pb2.RouteDiscovery()
            rd.ParseFromString(payload)

            route_forward = [node_id_to_hex(n) for n in rd.route] if rd.route else []
            snr_forward = list(rd.snr_towards) if rd.snr_towards else []
            route_back = [node_id_to_hex(n) for n in rd.route_back] if rd.route_back else []
            snr_back = list(rd.snr_back) if rd.snr_back else []

            # The traceroute response comes FROM the destination back TO the origin.
            # from_id here is the destination that replied, to_id is the origin.
            origin = to_id or "unknown"
            destination = from_id
            hop_count = len(route_forward)
            completed = hop_count > 0

            self.store.insert_traceroute(
                timestamp=rx_time,
                origin_id=origin,
                destination_id=destination,
                route_forward=route_forward,
                snr_forward=snr_forward,
                route_back=route_back,
                snr_back=snr_back,
                hop_count=hop_count,
                completed=completed,
            )

            route_str = " -> ".join([origin] + route_forward + [destination])
            log.info("Traceroute: %s (%d hops, completed=%s)", route_str, hop_count, completed)

        except Exception as e:
            log.warning("Failed to parse traceroute from %s: %s", from_id, e)
