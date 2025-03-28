from functools import _make_key
from typing import Dict, Tuple
import torch
import gc
import logging

from torch.nn.modules.module import Module
from .utils import singleton

logger = logging.getLogger(__name__)

CACHE_MAXSIZE = 100
class ModelValidChecker:
    def __init__(self, result):
        self.result = result
        # module dict should be the first place
        self.module = result[0] if isinstance(result, tuple) else result
        self.key_count = self.get_latest_key_count()

    def is_valid(self) -> bool:
        # if tensors on gpu are released, the module keys are incomplete
        if self.key_count == self.get_latest_key_count():
            return True
        return False

    def get_latest_key_count(self) -> int:
        if isinstance(self.module, torch.nn.Module):
            return len(self.module.state_dict().keys())
        elif isinstance(self.module, Dict):
            return len(self.module.keys())

        logger.warning(f"\033[92mModel_Cache: result is not torch.nn.Module or dict, but {type(self.module)}\033[0m")
        logger.warning(f"\033[92mModel_Cache: So cache will never happen for this result! \033[0m")
        # will nevel equal and never cache the result
        return -1

    def get_result(self) -> torch.nn.Module | Tuple[torch.nn.Module, ...]:
        return self.result

@singleton
class ModelCache:
    """
    A Model Cache Manager for all kinds of decorated functions.
    """
    def __init__(self):
        self.valid_checker_map : Dict[str, ModelValidChecker] = {}
        self.lru_cache : list[str] = []
        self.maxsize = CACHE_MAXSIZE
        logger.info(f"\033[92mModelCache: ModelCache is initialized with maxsize:{self.maxsize}\033[0m")

    def generate_cache_key(self, *args, **kwargs):
        args_key = _make_key(args, kwargs, typed=True)
        return args_key

    def cached(self, model_key) -> bool:
        checker = self.valid_checker_map.get(model_key, None)
        if checker:
            return checker.is_valid()
        return False

    def register_model(
        self,
        model_key: str,
        result: torch.nn.Module | Tuple[torch.nn.Module, ...],
    ) -> None:
        """
        Register an cache model result to be used in Comfy.

        :code:`result` can be either:

        - A :class:`torch.nn.Module` class directly referencing the model.
        - B :A tuple with `torch.nn.Module` Dict at the first place and other kwargs follow-up
        """
        if len(self.lru_cache) >= self.maxsize:
            # cache list reached maxsize and release host memory
            del_key = self.lru_cache.pop(0)
            del self.valid_checker_map[del_key]
            gc.collect()
            torch.cuda.empty_cache()

        self.valid_checker_map[model_key] = ModelValidChecker(result)
        if model_key in self.lru_cache:
            self.lru_cache.remove(model_key)
        self.lru_cache.append(model_key)

    def get_result(self, model_key) -> Module | Tuple[Module]:
        logger.info(f"\033[92mModel_Cache: Return a cached module result with args:{model_key}\033[0m")
        # update lru cache
        self.lru_cache.remove(model_key)
        self.lru_cache.append(model_key)

        return self.valid_checker_map[model_key].get_result()

def cache_model(func):
    def wrapper(*args, **kwargs):
        model_key = ModelCache().generate_cache_key(*args, **kwargs)
        if ModelCache().cached(model_key):
            return ModelCache().get_result(model_key)

        result = func(*args, **kwargs)
        ModelCache().register_model(model_key, result)
        return result
    return wrapper