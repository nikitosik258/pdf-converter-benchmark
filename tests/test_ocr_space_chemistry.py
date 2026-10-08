from pdf_benchmark.adapters.cloud.ocr_space_adapter import (
    OCRSpaceAdapter,
    _linear_formula_candidates,
    _markdown_chemical_structures,
)


def _parse(wrapper):
    adapter = OCRSpaceAdapter({})
    pages = {}
    order = adapter._parse_wrapper(wrapper, pages, [(100.0, 200.0)], 0)
    return pages[1], order


def test_linear_formula_candidates_keep_raw_ocr_without_chemical_correction():
    text = (
        "The PDF identifier D05 and variables U₁₁ B, P1 P4, and P1P2 are not chemistry. "
        "Mixtures FeCl3-6Н2О and FeSO4 (NH4)2SO4-6H₂O were used."
    )

    formulas = [item[0] for item in _linear_formula_candidates(text)]

    assert formulas == ["FeCl3-6Н2О", "FeSO4 (NH4)2SO4-6H₂O"]


def test_encoded_structure_expression_is_not_duplicated_as_linear_formula():
    text = (
        "![Structure](https://latex.codecogs.com/svg.latex?"
        "%5Cchem%7BNaO3S-C6H4-N%3DNC10H5%28OH%29SO3Na%7D)"
    )

    assert _linear_formula_candidates(text) == []
    structures = _markdown_chemical_structures(text)
    assert len(structures) == 1
    assert structures[0].expression == "NaO3S-C6H4-N=NC10H5(OH)SO3Na"
    assert structures[0].text_labels == ("N=N", "NaO3S", "OH", "SO3Na")


def test_explicit_markdown_chem_structures_become_spatial_chemical_objects():
    first_url = (
        "https://latex.codecogs.com/svg.latex?"
        "%5Cchem%7BNaO3S-C6H4-N%3DNC10H5%28OH%29SO3Na%7D"
    )
    second_url = (
        "https://latex.codecogs.com/svg.latex?"
        "%5Cchem%7BNaO3S-C6H3%28N%3DNC%28COONa%29%3DN-C6H4-SO3Na%29-OH%7D"
    )
    text = (
        "| Name | Structure |\n"
        "| --- | --- |\n"
        f"| Alpha dye | ![Alpha structure]({first_url}) |\n"
        f"| Beta dye | ![Beta structure]({second_url}) |\n"
    )
    wrapper = {
        "page_offset": 0,
        "response": {
            "ParsedResults": [{
                "ParsedText": text,
                "TextOverlay": {
                    "HasOverlay": True,
                    "Lines": [
                        {"LineText": "Alpha dye", "Words": [
                            {"WordText": "Alpha dye", "Left": 10, "Top": 80, "Width": 40, "Height": 20},
                        ]},
                        {"LineText": "NaO3S", "Words": [
                            {"WordText": "NaO3S", "Left": 70, "Top": 80, "Width": 30, "Height": 20},
                        ]},
                        {"LineText": "Beta dye", "Words": [
                            {"WordText": "Beta dye", "Left": 10, "Top": 240, "Width": 40, "Height": 20},
                        ]},
                        {"LineText": "SO3Na", "Words": [
                            {"WordText": "SO3Na", "Left": 90, "Top": 240, "Width": 30, "Height": 20},
                        ]},
                    ],
                },
            }],
        },
    }

    page, order = _parse(wrapper)

    assert len(page.chemical_objects) == 2
    assert order == len(page.text_blocks) + len(page.chemical_objects) + len(page.tables)
    assert all(obj.subtype == "structure" for obj in page.chemical_objects)
    assert all(obj.bbox is not None for obj in page.chemical_objects)
    assert page.chemical_objects[0].bbox.y_max == page.chemical_objects[1].bbox.y_min
    assert page.chemical_objects[0].asset_path == first_url
    assert page.chemical_objects[0].provenance["asset_reference_kind"] == (
        "remote_renderable_formula_uri"
    )
    assert page.chemical_objects[0].provenance["bbox_policy"] == "overlay_table_rows_v1"
