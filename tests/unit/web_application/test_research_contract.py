from pathlib import Path
import pytest
from app.demo_workflow.terminal import terminal_state
from tests.unit.web_application.test_summary_lifecycle import finalizer_fixture


@pytest.mark.parametrize('code', ['research_context_invalid','research_correction_exhausted',
    'research_judgment_invalid','research_state_invalid','workflow_replay_mismatch','workflow_model_output_invalid'])
async def test_new_failure_terminal_preserves_summary_and_releases_only_after_save(code):
    finalizer, view, deps, _, _, releases, _ = await finalizer_fixture()
    original = deps.durable.get_state
    async def state(*,run_id):
        return terminal_state(await original(run_id=run_id),code)
    deps.durable.get_state = state
    failed = await state(run_id=view.run_id)
    view = view.model_copy(update={'terminal_code':code,'delivery_status':failed.delivery_status,'cancel_requested':False})
    release = deps.definition.release_run
    async def checked_release(run_id):
        assert deps.store.rows[run_id].terminal_code == code
        await release(run_id)
    deps.definition.release_run = checked_release
    await finalizer.begin(view=view,execution_stopped=True)
    await finalizer.before_durable_close()
    assert deps.store.attempts == 1 and releases == [view.run_id]
    assert deps.store.rows[view.run_id].summary() is not None
    js = (Path(__file__).parents[3]/'app/web_application/static/app.js').read_text()
    assert code+':' in js
