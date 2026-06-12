"""
Amazon Bedrock LLM Client Adapter

Implements the BaseLLM interface for Amazon Bedrock (via boto3).

Installation:
    pip install boto3

Environment Variables:
    AWS_ACCESS_KEY_ID: AWS access key
    AWS_SECRET_ACCESS_KEY: AWS secret key
    AWS_REGION_NAME: AWS region (default: us-east-1)
"""

import os
import time
from typing import Optional, Dict, Any, List, Iterator, AsyncIterator

from .base import (
    BaseLLM, ProviderType, GenerationConfig, LLMResponse, 
    ModelInfo, ResponseFormat, _estimate_cost
)

try:
    import boto3
    import json
    AMAZON_AVAILABLE = True
except ImportError:
    AMAZON_AVAILABLE = False


class AmazonLLMClient(BaseLLM):
    """
    Amazon Bedrock LLM client using boto3.
    
    Usage:
        client = AmazonLLMClient(
            aws_access_key_id="...",
            aws_secret_access_key="...",
            region_name="us-east-1"
        )
        response = client.generate("Explain quantum computing")
        print(response.content)
    
    Latest Models (as of 2026):
        - amazon.nova-pro-v1: New Nova Pro - Multimodal with 300K context (April 2025)
        - amazon.nova-lite-v1: New Nova Lite - Cost-effective multimodal (April 2025)
        - amazon.nova-micro-v1: New Nova Micro - Lightweight text model (April 2025)
        
        Legacy models (older generation, still available):
        - amazon.titan-text-premium-v1: Titan Text Premium - Legacy model
        - amazon.titan-text-express-v1: Titan Text Express - Legacy model
    """

    DEFAULT_MODEL = "amazon.nova-pro-v1"
    
    MODEL_CONTEXT_WINDOWS = {
        "amazon.nova-pro-v1": 300_000,
        "amazon.nova-lite-v1": 300_000,
        "amazon.nova-micro-v1": 300_000,
        "amazon.titan-text-premium-v1": 8_192,
        "amazon.titan-text-express-v1": 8_192,
    }
    
    VISION_MODELS = {"amazon.nova-pro-v1", "amazon.nova-lite-v1"}

    def __init__(self, api_key: Optional[str] = None, **kwargs):
        self._api_key = api_key or os.getenv("AWS_ACCESS_KEY_ID")
        self._secret_key = kwargs.get("aws_secret_access_key") or os.getenv("AWS_SECRET_ACCESS_KEY")
        self._region = kwargs.get("region_name") or os.getenv("AWS_REGION_NAME", "us-east-1")
        self._config = kwargs
        self._client = None
        
        if not AMAZON_AVAILABLE:
            raise ImportError(
                "boto3 package is required. "
                "Install with: pip install boto3"
            )

    @property
    def provider_type(self) -> ProviderType:
        return ProviderType.AMAZON
    
    @property
    def default_model(self) -> str:
        return self.DEFAULT_MODEL

    def _initialize_client(self) -> None:
        if not self._api_key or not self._secret_key:
            raise ValueError(
                "AWS credentials are required. Provide aws_access_key_id and "
                "aws_secret_access_key, or set the AWS_ACCESS_KEY_ID and "
                "AWS_SECRET_ACCESS_KEY environment variables."
            )
        
        self._client = boto3.client(
            'bedrock-runtime',
            aws_access_key_id=self._api_key,
            aws_secret_access_key=self._secret_key,
            region_name=self._region
        )

    def _convert_config(self, config: Optional[GenerationConfig]) -> Dict[str, Any]:
        if config is None:
            config = GenerationConfig()
        
        bedrock_config = {}
        
        if config.max_tokens is not None:
            bedrock_config["maxTokens"] = config.max_tokens
        
        if config.temperature is not None:
            bedrock_config["temperature"] = config.temperature
        
        if config.top_p is not None:
            bedrock_config["topP"] = config.top_p
        
        if config.stop_sequences:
            bedrock_config["stopSequences"] = config.stop_sequences
        
        return bedrock_config

    def generate(
        self, 
        prompt: str, 
        config: Optional[GenerationConfig] = None,
        model: Optional[str] = None,
        **kwargs
    ) -> LLMResponse:
        start_time = time.time()
        
        try:
            self._ensure_initialized()
            
            model_id = model or self.default_model
            bedrock_config = self._convert_config(config)
            
            body = {
                "inputText": prompt,
                "textGenerationConfig": bedrock_config
            }
            
            response = self._client.invoke_model(
                modelId=model_id,
                contentType="application/json",
                accept="application/json",
                body=json.dumps(body)
            )
            
            latency = time.time() - start_time
            response_body = json.loads(response['body'].read())
            
            return LLMResponse(
                success=True,
                content=response_body.get("outputText", ""),
                model=model_id,
                provider="amazon",
                latency=latency,
                raw_response=response_body
            )
            
        except Exception as e:
            return LLMResponse(
                success=False,
                content="",
                model=model or self.default_model,
                provider="amazon",
                error=str(e),
                latency=time.time() - start_time
            )

    def generate_stream(
        self,
        prompt: str,
        config: Optional[GenerationConfig] = None,
        model: Optional[str] = None,
        **kwargs
    ) -> Iterator[str]:
        self._ensure_initialized()
        
        model_id = model or self.default_model
        bedrock_config = self._convert_config(config)
        
        body = {
            "inputText": prompt,
            "textGenerationConfig": bedrock_config
        }
        
        response = self._client.invoke_model_with_response_stream(
            modelId=model_id,
            contentType="application/json",
            accept="application/json",
            body=json.dumps(body)
        )
        
        for event in response['body']:
            if event.get('chunk'):
                chunk_data = json.loads(event['chunk']['bytes'])
                if 'outputText' in chunk_data:
                    yield chunk_data['outputText']

    def list_models(self) -> List[ModelInfo]:
        return [
            ModelInfo(
                id="amazon.nova-pro-v1",
                name="Amazon Nova Pro",
                provider="amazon",
                context_window=300_000,
                max_output_tokens=32_768,
                supports_vision=True,
                description="New multimodal model with 300K context (April 2025)"
            ),
            ModelInfo(
                id="amazon.nova-lite-v1",
                name="Amazon Nova Lite",
                provider="amazon",
                context_window=300_000,
                max_output_tokens=32_768,
                supports_vision=True,
                description="New cost-effective multimodal model (April 2025)"
            ),
            ModelInfo(
                id="amazon.nova-micro-v1",
                name="Amazon Nova Micro",
                provider="amazon",
                context_window=300_000,
                max_output_tokens=32_768,
                supports_vision=False,
                description="New lightweight text model (April 2025)"
            ),
            # Legacy models (older generation)
            ModelInfo(
                id="amazon.titan-text-premium-v1",
                name="Amazon Titan Text Premium (Legacy)",
                provider="amazon",
                context_window=8_192,
                max_output_tokens=4_096,
                supports_vision=False,
                description="LEGACY: Older generation text model - use Nova instead"
            ),
            ModelInfo(
                id="amazon.titan-text-express-v1",
                name="Amazon Titan Text Express (Legacy)",
                provider="amazon",
                context_window=8_192,
                max_output_tokens=4_096,
                supports_vision=False,
                description="LEGACY: Older generation text model - use Nova instead"
            ),
        ]
