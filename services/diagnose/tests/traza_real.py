"""Una traza REAL como fixture, no una inventada.

Son los siete spans de la traza `78ddf2c0…` tal y como ClickHouse los
devuelve: un `502` que el gateway de Prometheus devolvio a un cliente, con una
llamada saliente a `/oauth2/token` que dio `400` dentro.

Se copia del almacen en vez de escribirse a mano por una razon concreta: la
forma de las filas es el contrato con ClickHouse —`Events.Name` como columna
con punto, los mapas como objetos, `Duration` en nanosegundos y `Timestamp`
como texto sin zona— y un fixture escrito a mano acaba probando que mi idea de
esa forma es consistente conmigo mismo. Esta traza ademas trae los dos casos
que importan: spans auxiliares del middleware ASGI que COPIAN el estado de la
respuesta, y una llamada `Client` que es la unica informacion nueva.
"""

from __future__ import annotations

from typing import Any

TRAZA_DEL_502: list[dict[str, Any]] = [
    {
        "TraceId": "78ddf2c0b68609390c8ecf0abda29f7c",
        "SpanId": "17041c73a22079b0",
        "ParentSpanId": "",
        "SpanName": "POST /admin/api/assistant/chat",
        "SpanKind": "Server",
        "ServiceName": "gateway",
        "Duration": 202820000,
        "StatusCode": "Error",
        "StatusMessage": "",
        "Timestamp": "2026-10-25 08:02:47.784727000",
        "SpanAttributes": {
            "error.type": "502",
            "http.request.method": "POST",
            "http.route": "/admin/api/assistant/chat",
            "server.address": "127.0.0.1:8031",
            "server.port": "8031"
        },
        "Events.Name": [
            "audit.admin_action"
        ],
        "Events.Attributes": [
            {
                "argus.actor.kind": "user",
                "argus.component.role": "api",
                "argus.event": "audit.admin_action",
                "argus.feature": "admin",
                "argus.outcome": "error",
                "argus.tenant": "9b154d1bde622b3bb77b371cd9f304efc5ac596bb08dda3575c569f49a1d3fea",
                "enduser.pseudo.id": "9b154d1bde622b3bb77b371cd9f304efc5ac596bb08dda3575c569f49a1d3fea",
                "redaction.ignored.count": "2"
            }
        ]
    },
    {
        "TraceId": "78ddf2c0b68609390c8ecf0abda29f7c",
        "SpanId": "1857921a59e9f481",
        "ParentSpanId": "17041c73a22079b0",
        "SpanName": "auth.validate",
        "SpanKind": "Internal",
        "ServiceName": "gateway",
        "Duration": 7483000,
        "StatusCode": "Unset",
        "StatusMessage": "",
        "Timestamp": "2026-10-25 08:02:47.784992000",
        "SpanAttributes": {},
        "Events.Name": [],
        "Events.Attributes": []
    },
    {
        "TraceId": "78ddf2c0b68609390c8ecf0abda29f7c",
        "SpanId": "4bff3c34f52326d2",
        "ParentSpanId": "17041c73a22079b0",
        "SpanName": "POST /admin/api/assistant/chat http receive",
        "SpanKind": "Internal",
        "ServiceName": "gateway",
        "Duration": 12000,
        "StatusCode": "Unset",
        "StatusMessage": "",
        "Timestamp": "2026-10-25 08:02:47.795447000",
        "SpanAttributes": {},
        "Events.Name": [],
        "Events.Attributes": []
    },
    {
        "TraceId": "78ddf2c0b68609390c8ecf0abda29f7c",
        "SpanId": "fedfb573982b9de5",
        "ParentSpanId": "17041c73a22079b0",
        "SpanName": "POST",
        "SpanKind": "Client",
        "ServiceName": "gateway",
        "Duration": 189165000,
        "StatusCode": "Error",
        "StatusMessage": "",
        "Timestamp": "2026-10-25 08:02:47.796142000",
        "SpanAttributes": {
            "error.type": "400",
            "http.request.method": "POST",
            "server.address": "127.0.0.1",
            "server.port": "9000",
            "url.full": "http://127.0.0.1:9000/oauth2/token"
        },
        "Events.Name": [],
        "Events.Attributes": []
    },
    {
        "TraceId": "78ddf2c0b68609390c8ecf0abda29f7c",
        "SpanId": "4ec15b61e51401fd",
        "ParentSpanId": "17041c73a22079b0",
        "SpanName": "POST /admin/api/assistant/chat http send",
        "SpanKind": "Internal",
        "ServiceName": "gateway",
        "Duration": 12000,
        "StatusCode": "Error",
        "StatusMessage": "",
        "Timestamp": "2026-10-25 08:02:47.987350000",
        "SpanAttributes": {
            "error.type": "502"
        },
        "Events.Name": [],
        "Events.Attributes": []
    },
    {
        "TraceId": "78ddf2c0b68609390c8ecf0abda29f7c",
        "SpanId": "1797e7b708f30ea7",
        "ParentSpanId": "17041c73a22079b0",
        "SpanName": "POST /admin/api/assistant/chat http send",
        "SpanKind": "Internal",
        "ServiceName": "gateway",
        "Duration": 4000,
        "StatusCode": "Unset",
        "StatusMessage": "",
        "Timestamp": "2026-10-25 08:02:47.987432000",
        "SpanAttributes": {},
        "Events.Name": [],
        "Events.Attributes": []
    },
    {
        "TraceId": "78ddf2c0b68609390c8ecf0abda29f7c",
        "SpanId": "c371cfba9a19329f",
        "ParentSpanId": "17041c73a22079b0",
        "SpanName": "POST /admin/api/assistant/chat http send",
        "SpanKind": "Internal",
        "ServiceName": "gateway",
        "Duration": 4000,
        "StatusCode": "Unset",
        "StatusMessage": "",
        "Timestamp": "2026-10-25 08:02:47.987534000",
        "SpanAttributes": {},
        "Events.Name": [],
        "Events.Attributes": []
    }
]
