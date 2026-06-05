from dataclasses import dataclass


@dataclass(frozen=True)
class ProcessTerminalizationTasksResult:
    claimed_count: int
    succeeded_count: int
    retrying_count: int


@dataclass(frozen=True)
class ExpireOrdersResult:
    expired_count: int
