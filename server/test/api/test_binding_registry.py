from api.session import BindingRegistry
from protocol.enums import BindingStatus
from protocol.models.device import DeviceBinding



def _binding(glass: str, phone: str) -> DeviceBinding:
    return DeviceBinding(
        binding_id=f"bind_{glass}_{phone}",
        glass_device_id=glass,
        phone_device_id=phone,
        status=BindingStatus.ACTIVE,
        created_at="2026-04-07T22:00:00+08:00",
        updated_at="2026-04-07T22:00:00+08:00",
    )



def test_binding_registry_enforces_one_phone_to_one_glass() -> None:
    registry = BindingRegistry()
    registry.upsert(_binding("dev_glass_001", "dev_phone_001"))
    registry.upsert(_binding("dev_glass_002", "dev_phone_001"))

    assert registry.get_by_glass("dev_glass_001") is None
    assert registry.get_active_phone("dev_glass_002") == "dev_phone_001"
