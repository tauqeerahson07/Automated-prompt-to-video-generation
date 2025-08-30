import json
from ..models import WorkflowCheckpoint

class CheckpointWrapper:
    def __init__(self, checkpoint, config, parent_config=None, metadata=None, pending_writes=None):
        self.checkpoint = checkpoint
        self.config = config
        self.parent_config = parent_config
        self.metadata = metadata or {}
        self.pending_writes = pending_writes

class DjangoCheckpointSaver:
    def get_tuple(self, config, *args, **kwargs):
        thread_id = config["configurable"]["thread_id"]
        obj = (
            WorkflowCheckpoint.objects
            .filter(thread_id=thread_id)
            .order_by("-version")
            .first()
        )
        if not obj:
            return None

        state = json.loads(obj.state_json or "{}")
        step = 0
        if isinstance(state, dict):
            step = state.get("step") or state.get("current_step") or 0

        return CheckpointWrapper(
            checkpoint=state,
            config=config,
            parent_config=None,
            metadata={"step": step, "version": obj.version},
            pending_writes=None
        )

    def save_tuple(self, config, state, *args, **kwargs):
        thread_id = config["configurable"]["thread_id"]
        latest = (
            WorkflowCheckpoint.objects
            .filter(thread_id=thread_id)
            .order_by("-version")
            .first()
        )
        next_version = (latest.version + 1) if latest else 1
        state_json = json.dumps(state or {})

        obj = WorkflowCheckpoint.objects.create(
            thread_id=thread_id,
            version=next_version,
            state_json=state_json,
        )
        return obj
    
    
    def get_next_version(self, config, *args, **kwargs):
        if config is None:
            return 1  # Default to version 1 if no config is provided
        if isinstance(config, dict) and "configurable" in config:
            thread_id = config["configurable"]["thread_id"]
        elif isinstance(config, str) or isinstance(config, int):
            thread_id = str(config)
        else:
            raise ValueError(f"Unsupported config type in get_next_version: {type(config)}")

        latest = (
            WorkflowCheckpoint.objects
            .filter(thread_id=thread_id)
            .order_by("-version")
            .first()
        )
        return (latest.version + 1) if latest else 1

    def get_latest_version(self, config, *args, **kwargs):
        if config is None:
            return 0  # Default to version 0 if no config is provided
        if isinstance(config, dict) and "configurable" in config:
            thread_id = config["configurable"]["thread_id"]
        elif isinstance(config, str) or isinstance(config, int):
            thread_id = str(config)
        else:
            raise ValueError(f"Unsupported config type in get_latest_version: {type(config)}")

        latest = (
            WorkflowCheckpoint.objects
            .filter(thread_id=thread_id)
            .order_by("-version")
            .first()
        )
        return latest.version if latest else 0
    
    # def get_by_version(self, thread_id: str, version: int):
    #     """Fetch a specific checkpoint version for a given thread_id."""
    #     obj = WorkflowCheckpoint.objects.filter(thread_id=thread_id, version=version).first()
    #     if not obj:
    #         return None

    #     state = json.loads(obj.state_json or "{}")
    #     step = state.get("step") or state.get("current_step") or 0 if isinstance(state, dict) else 0

    #     return CheckpointWrapper(
    #         checkpoint=state,
    #         config={"configurable": {"thread_id": thread_id}},
    #         parent_config=None,
    #         metadata={"step": step, "version": obj.version},
    #         pending_writes=None
    #     )
    
    def get_by_version(self, thread_id: str, version: int):
        obj = WorkflowCheckpoint.objects.filter(thread_id=thread_id, version=version).first()
        if not obj:
            return None

        state = json.loads(obj.state_json or "{}")
        return state  # ✅ return raw dict, not wrapper


    def get(self, config, *args, **kwargs):
        result = self.get_tuple(config)
        return result

    def put(self, config, state, *args, **kwargs):
        return self.save_tuple(config, state)

    def put_writes(self, *args, **kwargs):
        pass

    def get_writes(self, *args, **kwargs):
        return []

checkpointer = DjangoCheckpointSaver()
