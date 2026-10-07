import operator
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from routegis.errors import ConfigurationError
from routegis.providers.vworld.taxonomy import TaxonomyEntry, load_vworld_taxonomy


def test_load_taxonomy_normalizes_ids_and_exposes_provenance(fixture_directory: Path):
    taxonomy = load_vworld_taxonomy(fixture_directory / "vworld_taxonomy.yaml")

    assert taxonomy.schema_version == 1
    assert taxonomy.source_logical_service_count == 4
    assert taxonomy.expanded_wfs_id_count == 4
    assert taxonomy.get("LT_C_UO301") == TaxonomyEntry(
        official_name="국가유산 지정/보호구역",
        reference_category="국가유산",
    )
    assert taxonomy.aliases_for("국가유산") == ("문화유산", "문화재")


def test_loaded_taxonomy_is_immutable(fixture_directory: Path):
    taxonomy = load_vworld_taxonomy(fixture_directory / "vworld_taxonomy.yaml")
    entry = taxonomy.get("lt_c_uo301")

    assert entry is not None
    with pytest.raises(FrozenInstanceError):
        setattr(entry, "official_name", "변경")
    with pytest.raises(TypeError):
        operator.setitem(taxonomy.layers, "new", entry)
    with pytest.raises(TypeError):
        operator.setitem(taxonomy.categories, "new", ())


@pytest.mark.parametrize(
    "content",
    [
        "layers: [",
        "- not-a-mapping\n",
        "schema_version: 1\n",
        """
schema_version: 1
source_url: https://example.com
retrieved_at: 2026-09-23
source_logical_service_count: 1
expanded_wfs_id_count: 1
categories:
  국가유산:
    aliases: []
layers:
  lt_c_uo301:
    name: 국가유산 지정/보호구역
    category: 국가유산
""",
        """
schema_version: 1
source_url: https://example.com
retrieved_at: "2026-09-23"
source_logical_service_count: 1
expanded_wfs_id_count: 1
categories:
  국가유산:
    aliases: [""]
layers:
  lt_c_uo301:
    name: 국가유산 지정/보호구역
    category: 국가유산
""",
        """
schema_version: 1
source_url: https://example.com
retrieved_at: "2026-09-23"
source_logical_service_count: 1
expanded_wfs_id_count: 1
categories:
  국가유산:
    aliases: []
layers:
  lt_c_uo301:
    name: 국가유산 지정/보호구역
    category: 미등록분야
""",
        """
schema_version: 1
source_url: https://example.com
retrieved_at: "2026-09-23"
source_logical_service_count: 1
expanded_wfs_id_count: 2
categories:
  국가유산:
    aliases: []
layers:
  lt_c_uo301:
    name: 국가유산 지정/보호구역
    category: 국가유산
""",
        """
schema_version: 1
source_url: https://example.com
retrieved_at: "2026-09-23"
source_logical_service_count: 1
expanded_wfs_id_count: 1
categories:
  국가유산:
    aliases: []
layers:
  lt_c_uo301:
    name: 첫 번째
    category: 국가유산
  lt_c_uo301:
    name: 두 번째
    category: 국가유산
""",
        """
schema_version: 1
source_url: https://example.com
retrieved_at: "2026-09-23"
source_logical_service_count: 2
expanded_wfs_id_count: 2
categories:
  국가유산:
    aliases: []
layers:
  lt_c_uo301:
    name: 첫 번째
    category: 국가유산
  LT_C_UO301:
    name: 두 번째
    category: 국가유산
""",
    ],
)
def test_invalid_taxonomy_raises_configuration_error(tmp_path: Path, content: str):
    path = tmp_path / "invalid_taxonomy.yaml"
    path.write_text(content, encoding="utf-8")

    with pytest.raises(ConfigurationError) as error:
        load_vworld_taxonomy(path)

    assert path.name in str(error.value)


def test_missing_taxonomy_raises_configuration_error(tmp_path: Path):
    path = tmp_path / "missing_taxonomy.yaml"

    with pytest.raises(ConfigurationError) as error:
        load_vworld_taxonomy(path)

    assert path.name in str(error.value)


def test_packaged_taxonomy_is_complete_and_independent_of_working_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.chdir(tmp_path)

    taxonomy = load_vworld_taxonomy()

    assert taxonomy.source_logical_service_count == 169
    assert taxonomy.expanded_wfs_id_count == len(taxonomy.layers) == 174
    assert taxonomy.expanded_wfs_id_count >= taxonomy.source_logical_service_count
    expected = {
        "lt_c_adsido_info": ("광역시도", "경계"),
        "lt_c_ud801": ("개발제한구역", "용도지역지구"),
        "lt_l_gimsfault": ("단층", "자연"),
        "lt_c_uo301": ("국가유산 지정/보호구역", "국가유산"),
        "lt_p_dgmuseumart": ("박물관미술관", "국가유산"),
        "lt_c_uo501": ("전통사찰보존", "국가유산"),
        "lt_l_aiscorrid_ys": ("한강회랑", "항공·공항"),
        "lt_l_aiscorrid_gj": ("한강회랑", "항공·공항"),
        "lt_p_aiscorrid_ys": ("한강회랑", "항공·공항"),
        "lt_p_aiscorrid_gj": ("한강회랑", "항공·공항"),
    }
    for data_id, (official_name, reference_category) in expected.items():
        entry = taxonomy.get(data_id)
        assert entry is not None
        assert entry.official_name == official_name
        assert entry.reference_category == reference_category

    assert taxonomy.aliases_for("국가유산") == ("문화유산", "문화재")
    assert all(data_id and entry.official_name and entry.reference_category for data_id, entry in taxonomy.layers.items())
    assert all(alias for aliases in taxonomy.categories.values() for alias in aliases)
