def test_health_degraded_but_alive(make_client):
    with make_client() as c:
        h = c.get("/api/v1/health").json()
        states = {m["key"]: m["state"] for m in h["modules"]}
        assert h["database"] is True
        assert h["status"] == "degraded"                      # failing + broken modules
        assert states["good"] == "running" and states["failing"] == "failed"
        assert c.get("/api/v1/good/ping").json() == {"pong": True}


def test_camel_model_round_trip():
    from control_center.core.schemas import CamelModel

    class Sample(CamelModel):
        gpu_ratio: float

    assert Sample(gpu_ratio=0.5).model_dump(by_alias=True) == {"gpuRatio": 0.5}   # JSON side
    assert Sample.model_validate({"gpuRatio": 1.0}).gpu_ratio == 1.0              # Angular -> Python


def test_disabled_module_not_mounted(make_client, settings):
    mods = settings.modules.model_copy(update={"disabled": ["good"]})
    with make_client(modules=mods) as c:
        assert c.get("/api/v1/good/ping").status_code == 404
        menu = {m["key"]: m["state"] for m in c.get("/api/v1/meta/modules").json()}
        assert menu["good"] == "disabled"
        assert c.app.state.ctx.service("good.lookup") is None    # consumers see None, not a crash


def test_service_registered(make_client):
    with make_client() as c:
        assert c.app.state.ctx.service("good.lookup")("x") == "info:x"


def test_operation_ids_readable_and_unique(make_client):
    with make_client() as c:
        spec = c.get("/openapi.json").json()
    ids = [op["operationId"] for path in spec["paths"].values() for op in path.values()]
    assert {"core_health", "core_modules", "good_ping"} <= set(ids)
    assert "/api/v1/stream" not in spec["paths"]
    assert len(ids) == len(set(ids))      # duplicates break the Angular generator
