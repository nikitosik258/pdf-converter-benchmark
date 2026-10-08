from pdf_benchmark.adapters.cloud.ocr_space_adapter import OCRSpaceAdapter


def _parse(wrapper, *, config=None):
    adapter = OCRSpaceAdapter(config or {})
    pages = {}
    order = adapter._parse_wrapper(wrapper, pages, [(100.0, 200.0)], 0)
    return pages[1], order


def test_overlay_lines_become_spatial_text_blocks():
    wrapper = {
        "page_offset": 0,
        "response": {
            "ParsedResults": [{
                "ParsedText": "Alpha beta\nGamma",
                "TextOverlay": {
                    "HasOverlay": True,
                    "Lines": [
                        {
                            "LineText": "Alpha beta",
                            "Words": [
                                {"WordText": "Alpha", "Left": 20, "Top": 40, "Width": 30, "Height": 20},
                                {"WordText": "beta", "Left": 60, "Top": 40, "Width": 40, "Height": 20},
                            ],
                        },
                        {
                            "Words": [
                                {"WordText": "Gamma", "Left": 10, "Top": 80, "Width": 50, "Height": 20},
                            ],
                        },
                    ],
                },
            }],
        },
    }

    page, order = _parse(wrapper)

    assert order == 2
    assert [block.raw_text for block in page.text_blocks] == ["Alpha beta", "Gamma"]
    assert page.reading_order == ["ocrspace_p1_line_0000", "ocrspace_p1_line_0001"]
    assert page.text_blocks[0].bbox.as_list == [0.1, 0.1, 0.5, 0.15]
    assert page.text_blocks[0].provenance["raw_pixel_bbox"] == {
        "x_min": 20.0, "y_min": 40.0, "x_max": 100.0, "y_max": 60.0,
    }
    assert page.text_blocks[0].provenance["pixel_canvas"] == {
        "width": 200.0, "height": 400.0, "pixels_per_pdf_point": 2.0,
    }


def test_parsed_text_is_segmented_when_overlay_is_missing():
    wrapper = {
        "page_offset": 0,
        "response": {
            "ParsedResults": [{
                "ParsedText": "Heading\n\nFirst paragraph line one.\nLine two.\n\nThird paragraph.",
                "TextOverlay": {
                    "HasOverlay": False,
                    "Lines": [],
                    "Message": "Overlay requested but no regions were detected.",
                },
            }],
        },
    }

    page, order = _parse(wrapper, config={"parsed_text_max_block_chars": 128})

    assert order == 3
    assert [block.raw_text for block in page.text_blocks] == [
        "Heading",
        "First paragraph line one.\nLine two.",
        "Third paragraph.",
    ]
    assert all(block.bbox is None for block in page.text_blocks)
    assert all(block.provenance["source"] == "ParsedText.segment" for block in page.text_blocks)


def test_long_overlayless_paragraph_is_bounded_without_dropping_text():
    text = " ".join(f"token{i}" for i in range(80))
    wrapper = {
        "page_offset": 0,
        "response": {
            "ParsedResults": [{
                "ParsedText": text,
                "TextOverlay": {"HasOverlay": False, "Lines": []},
            }],
        },
    }

    page, order = _parse(wrapper, config={"parsed_text_max_block_chars": 128})

    assert order == len(page.text_blocks) > 1
    assert all(len(block.raw_text) <= 128 for block in page.text_blocks)
    assert " ".join(block.raw_text for block in page.text_blocks) == text


def test_markdown_tables_still_use_full_parsed_text():
    wrapper = {
        "page_offset": 0,
        "response": {
            "ParsedResults": [{
                "ParsedText": "| Name | Value |\n| --- | --- |\n| alpha | 42 |\n",
                "TextOverlay": {"HasOverlay": False, "Lines": []},
            }],
        },
    }

    page, order = _parse(wrapper)

    assert len(page.text_blocks) == 1
    assert len(page.tables) == 1
    assert page.tables[0].rows == 2
    assert page.tables[0].columns == 2
    assert order == 2
