from agentai.retrieval import explicit_targets, scope_records


def test_php_filename_narrows_pdf_bundle_and_missing_file_stays_empty():
    rows = [
        {'source': 'docs', 'citation': 'document:docs/module.pdf?page=1#L1-L3',
         'text': 'AvailableReports.php каталог и доступ'},
        {'source': 'docs', 'citation': 'document:docs/other.pdf?page=1#L1-L3', 'text': 'noise'},
    ]
    selected, targeted = scope_records(rows, 'Покажи AvailableReports.php')
    assert targeted and selected == rows[:1]
    assert scope_records(rows, 'Покажи missing.php') == ([], True)


def test_file_extension_is_not_treated_as_an_identifier_scope():
    files, identifiers = explicit_targets('Покажи _param_form.php')
    assert files == ['_param_form.php']
    assert identifiers == []


def test_wrapped_pdf_identifier_is_not_reported_missing():
    rows = [
        {'source': 'docs', 'citation': 'document:docs/fields.pdf?page=1#L1-L3',
         'text': 'Флаг all_store flag_all_\nstore\nint Право для просмотра отчетов'},
        {'source': 'docs', 'citation': 'document:docs/other.pdf?page=1#L1-L3',
         'text': 'flag_all_storage unrelated'},
    ]
    assert scope_records(rows, 'Что означает flag_all_store?') == (rows[:1], True)


def test_filename_inside_bundle_requires_a_whole_filename():
    rows = [{'source': 'docs', 'citation': 'bundle.pdf', 'text': 'NotAvailableReports.php'}]
    assert scope_records(rows, 'Покажи AvailableReports.php') == ([], True)
