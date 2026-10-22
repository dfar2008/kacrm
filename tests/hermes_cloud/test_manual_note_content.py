"""The actual NoteForm payload must survive normalization into the event log."""
from hermes_cloud.app.touchpoints.manual import ManualNoteAdapter


def test_note_form_content_and_progress_are_preserved():
    note = '已整理部署资料，待确认客户最关心的场景。'
    normalized = ManualNoteAdapter().normalize({
        'module': 'accounts', 'record_id': 1, 'user_id': 7,
        'content': note, 'progress': '资料已整理',
        'next_contact_date': '2026-10-23',
    })
    assert normalized.content['text'] == note
    assert normalized.content['progress'] == '资料已整理'
    assert normalized.content['summary'] == '资料已整理'
    assert normalized.content['next_contact_date'] == '2026-10-23'
    assert normalized.subjects == [{'module': 'accounts', 'id': 1}]


def test_explicit_text_takes_precedence_over_content_alias():
    normalized = ManualNoteAdapter().normalize({'text': '原文', 'content': '兼容字段'})
    assert normalized.content['text'] == '原文'
