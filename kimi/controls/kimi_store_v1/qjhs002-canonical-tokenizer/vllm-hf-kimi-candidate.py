# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Copyright contributors to the vLLM project
import contextlib
import copy
import json
from pathlib import Path
from typing import TypeAlias

from transformers import AutoTokenizer, PreTrainedTokenizer, PreTrainedTokenizerFast
from transformers import __version__ as transformers_version
from transformers.dynamic_module_utils import get_class_from_dynamic_module

from vllm.transformers_utils.config import get_sentence_transformer_tokenizer_config

from .protocol import TokenizerLike

HfTokenizer: TypeAlias = PreTrainedTokenizer | PreTrainedTokenizerFast


def get_cached_tokenizer(tokenizer: HfTokenizer) -> HfTokenizer:
    """
    By default, transformers will recompute multiple tokenizer properties
    each time they are called, leading to a significant slowdown.
    This proxy caches these properties for faster access.
    """
    cached_tokenizer = copy.copy(tokenizer)

    tokenizer_all_special_ids = tokenizer.all_special_ids
    tokenizer_all_special_tokens = tokenizer.all_special_tokens
    tokenizer_vocab = tokenizer.get_vocab()
    tokenizer_len = len(tokenizer)

    max_token_id = max(tokenizer_vocab.values())
    max_chars_per_token = max(len(tok) for tok in tokenizer_vocab)

    # Some tokenizers (e.g., QwenTokenizer) have special tokens that
    # are added and included in the implementation of the vocab_size
    # property, but not in get_vocab(); if there is an implementation
    # of vocab size, we should take the greater value.
    if hasattr(tokenizer, "vocab_size"):
        with contextlib.suppress(NotImplementedError):
            max_token_id = max(max_token_id, tokenizer.vocab_size)

    class CachedTokenizer(tokenizer.__class__):  # type: ignore
        @property
        def all_special_ids(self) -> list[int]:
            return tokenizer_all_special_ids

        @property
        def all_special_tokens(self) -> list[str]:
            return tokenizer_all_special_tokens

        @property
        def max_token_id(self) -> int:
            return max_token_id

        @property
        def max_chars_per_token(self) -> int:
            return max_chars_per_token

        def get_vocab(self) -> dict[str, int]:
            return tokenizer_vocab

        def __len__(self) -> int:
            return tokenizer_len

        def __reduce__(self):
            return get_cached_tokenizer, (tokenizer,)

    CachedTokenizer.__name__ = f"Cached{tokenizer.__class__.__name__}"

    cached_tokenizer.__class__ = CachedTokenizer
    return cached_tokenizer


class CachedHfTokenizer(TokenizerLike):
    @classmethod
    def from_pretrained(
        cls,
        path_or_repo_id: str | Path,
        *args,
        trust_remote_code: bool = False,
        revision: str | None = None,
        download_dir: str | None = None,
        **kwargs,
    ) -> HfTokenizer:
        try:
            tokenizer_cls = AutoTokenizer
            if trust_remote_code is True and transformers_version == "5.5.1":
                tokenizer_path = Path(path_or_repo_id) / (kwargs.get("subfolder") or "")
                if tokenizer_path.is_dir() and (tokenizer_path / "config.json").is_file():
                    with (tokenizer_path / "config.json").open(encoding="utf-8") as config_file:
                        model_type = json.load(config_file).get("model_type")
                    if model_type == "kimi_k25":
                        with (tokenizer_path / "tokenizer_config.json").open(encoding="utf-8") as config_file:
                            tokenizer_config = json.load(config_file)
                        class_reference = "tokenization_kimi.TikTokenTokenizer"
                        if (
                            tokenizer_config.get("tokenizer_class") != "TikTokenTokenizer"
                            or tokenizer_config.get("auto_map", {}).get("AutoTokenizer")
                            != [class_reference, None]
                        ):
                            raise ValueError("Unexpected local Kimi tokenizer class or auto_map")
                        tokenizer_cls = get_class_from_dynamic_module(
                            class_reference,
                            str(tokenizer_path),
                            revision=revision,
                            cache_dir=download_dir,
                            **{
                                key: value
                                for key, value in kwargs.items()
                                if key in (
                                    "code_revision", "local_files_only", "token",
                                    "force_download", "proxies",
                                )
                            },
                        )
            tokenizer = tokenizer_cls.from_pretrained(
                path_or_repo_id,
                *args,
                trust_remote_code=trust_remote_code,
                revision=revision,
                cache_dir=download_dir,
                **kwargs,
            )
        except ValueError as e:
            # If the error pertains to the tokenizer class not existing or not
            # currently being imported,
            # suggest using the --trust-remote-code flag.
            if not trust_remote_code and (
                "does not exist or is not currently imported." in str(e)
                or "requires you to execute the tokenizer file" in str(e)
            ):
                err_msg = (
                    "Failed to load the tokenizer. If the tokenizer "
                    "is a custom tokenizer not yet available in the "
                    "HuggingFace transformers library, consider "
                    "setting `trust_remote_code=True` in LLM or using "
                    "the `--trust-remote-code` flag in the CLI."
                )
                raise RuntimeError(err_msg) from e
            else:
                raise e

        # The special_tokens in tokenizer should also be
        # controlled by do_lower_case in encoder_config
        encoder_config = get_sentence_transformer_tokenizer_config(
            path_or_repo_id, revision
        )
        if isinstance(encoder_config, dict) and encoder_config.get(
            "do_lower_case", False
        ):
            special_tokens_map = {
                k: v.lower() for k, v in tokenizer.special_tokens_map.items()
            }
            tokenizer.add_special_tokens(special_tokens_map)

        return get_cached_tokenizer(tokenizer)
