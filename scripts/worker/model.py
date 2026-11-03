"""Export the existing X3D artifact once; inference needs no training modules."""
import hashlib
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
CLASSES = ['take', 'put', 'touch', 'background']
FORMAT = 'sentient.action.x3d/1'


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024*1024), b''):
            digest.update(block)
    return digest.hexdigest()


def export_model(source, output):
    """Export trusted local V2/V5 weights with numerical parity and batch checks."""
    import torch
    from torch import nn
    import torch.nn.functional as F
    sys.path.insert(0, str(ROOT/'vendor/pytorchvideo'))
    from pytorchvideo.models.hub.x3d import x3d_s

    class KNNHead(nn.Module):
        def __init__(self, size, k, amount):
            super().__init__()
            self.base = nn.Sequential(nn.LayerNorm(2048, elementwise_affine=False),
                                      nn.Dropout(.2), nn.Linear(2048, 4))
            self.register_buffer('bank', torch.empty(size, 2048))
            self.register_buffer('targets', torch.empty(size, dtype=torch.long))
            self.register_buffer('class_weight', torch.ones(4))
            self.k, self.amount = k, amount

        def forward(self, x):
            original = self.base(x)
            value = F.normalize(F.layer_norm(x, (2048,)), dim=1)
            top, indices = (value @ self.bank.T).topk(self.k, dim=1)
            weights = ((top-top[:, :1])/.07).exp()
            votes = x.new_full((x.size(0), 4), .01)
            votes.scatter_add_(1, self.targets[indices], weights*self.class_weight[self.targets[indices]])
            other = votes/votes.sum(1, keepdim=True)
            return ((1-self.amount)*original.softmax(1)+self.amount*other).clamp_min(1e-7).log()

    class TakeHead(nn.Module):
        def __init__(self, base, state):
            super().__init__();self.base=base
            self.take=nn.Sequential(nn.LayerNorm(2048,elementwise_affine=False),nn.Linear(2048,128),nn.GELU(),nn.Dropout(.25),nn.Linear(128,1))
            self.take.load_state_dict(state)
        def forward(self,x):
            other=self.base(x)[:,1:].softmax(1)
            take=self.take(x).sigmoid()
            return torch.cat([take,(1-take)*other],dim=1).clamp_min(1e-7).log()

    class TraceableSwish(nn.Module):
        def forward(self, x):
            return x*x.sigmoid()

    def replace_swish(module):
        for name, child in list(module.named_children()):
            if child.__class__.__name__ == 'Swish':
                setattr(module, name, TraceableSwish())
            else:
                replace_swish(child)

    class WorkerModel(nn.Module):
        def __init__(self, backbone, head, bias):
            super().__init__()
            self.backbone, self.head = backbone, head
            self.register_buffer('bias', torch.tensor(bias, dtype=torch.float32))

        def forward(self, frames):
            return self.head(self.backbone(frames))+self.bias

    source, output = Path(source).resolve(), Path(output).resolve()
    if output.exists():
        raise ValueError('Use a fresh model export directory')
    torch.set_num_threads(2)
    started = time.monotonic()
    artifact = torch.load(source, weights_only=True, map_location='cpu')
    if artifact['classes'] != CLASSES or artifact['input_shape'] != [3, 13, 160, 160]:
        raise ValueError('Unsupported model classes or input shape')
    backbone = x3d_s(pretrained=False)
    backbone.blocks[-1].proj = nn.Identity()
    backbone.blocks[-1].activation = nn.Identity()
    backbone.load_state_dict(artifact['backbone_state'])
    if artifact.get('head_kind', 'classifier') == 'corrected':
        spec = artifact['head_spec']
        if spec['kind'] != 'knn':
            raise ValueError('This exporter supports the selected KNN V5 head or the V2 classifier')
        head = KNNHead(artifact['bank_size'], spec['k'], spec['amount'])
    else:
        head = nn.Sequential(nn.LayerNorm(2048, elementwise_affine=False), nn.Dropout(.2), nn.Linear(2048, 4))
    head.load_state_dict(artifact['head_state'])
    if artifact.get('take_classifier_state') is not None:
        head=TakeHead(head,artifact['take_classifier_state'])
    replace_swish(backbone)
    model = WorkerModel(backbone, head, artifact.get('bias', [0.]*4)).eval().requires_grad_(False)
    torch.manual_seed(8901)
    example = torch.rand(1, 3, 13, 160, 160)
    with torch.inference_mode():
        traced = torch.jit.trace(model, example, check_trace=True)
        differences = []
        for batch in [1, 2, 3]:
            sample = torch.rand(batch, 3, 13, 160, 160)
            differences.append(float((model(sample)-traced(sample)).abs().max()))
        if max(differences)>1e-5:
            raise ValueError('Export does not preserve eager model outputs')
        # Check the selected research head independently, including its learned bank.
        if artifact.get('head_kind') == 'corrected':
            from improve_v5 import CorrectedHead
            original = CorrectedHead(artifact['head_spec'], artifact['bank_size']).eval()
            original.load_state_dict(artifact['head_state'])
            if artifact.get('take_classifier_state') is not None:original=TakeHead(original,artifact['take_classifier_state']).eval()
            features = torch.randn(5, 2048)
            head_error = float((original(features)-head(features)).abs().max())
            if head_error>1e-6:
                raise ValueError('Portable head differs from research head')
        else:
            head_error = 0.
    output.mkdir(parents=True)
    traced.save(str(output/'model.pt'))
    metadata = {
        'format':FORMAT, 'model_id':('x3d-take-demo-' if artifact.get('pickup_policy') else 'x3d-review-')+sha256(source)[:20],
        'source_checkpoint_sha256':sha256(source), 'model_sha256':sha256(output/'model.pt'),
        'classes':CLASSES, 'input_shape':[3, 13, 160, 160],
        'mean':artifact['normalization_mean'], 'std':artifact['normalization_std'],
        'crop':'fixed square around selected subject bounds, side=max(extent,min(W,H)*.25)*1.4; PIL black padding and bilinear resize',
        'training_input':'13 sparse frame-index samples with supplied subject poses; detector boxes and continuous timing are a transfer experiment',
        'calibration':'uncalibrated', 'intended_use':'human review triage, not confirmed action detection or theft inference',
        'source_selection':artifact['selection'], 'research_head_max_abs_error':head_error,
        'eager_jit_max_abs_error_by_batch':dict(zip(['1','2','3'], differences)),
        'torch_version':torch.__version__, 'export_seconds':time.monotonic()-started,
        'pickup_policy':artifact.get('pickup_policy'),
        'data_terms':'RetailAction custom license applies to trained weights; see source dataset LICENSE',
        'runtime_dependencies':['torch','numpy','av','Pillow'],
    }
    (output/'manifest.json').write_text(json.dumps(metadata, indent=2)+'\n')
    return metadata


class Runtime:
    def __init__(self, directory, device='auto'):
        import torch
        self.torch = torch
        folder = Path(directory)
        self.metadata = json.loads((folder/'manifest.json').read_text())
        if self.metadata['format'] != FORMAT or self.metadata['classes'] != CLASSES:
            raise ValueError('Unsupported worker artifact')
        if sha256(folder/'model.pt') != self.metadata['model_sha256']:
            raise ValueError('Model artifact checksum mismatch')
        self.device = ('cuda' if torch.cuda.is_available() else 'cpu') if device=='auto' else device
        if self.device not in ('cuda','cpu'):
            raise ValueError('Device must be auto, cuda, or cpu')
        if self.device=='cuda' and not torch.cuda.is_available():
            raise ValueError('CUDA was requested but is unavailable')
        torch.set_num_threads(2)
        torch.use_deterministic_algorithms(True)
        torch.backends.cuda.matmul.allow_tf32=False
        torch.backends.cudnn.allow_tf32=False
        torch.backends.cudnn.benchmark=False
        self.model = torch.jit.load(str(folder/'model.pt'), map_location=self.device).eval()
        if self.device=='cuda':
            torch.cuda.reset_peak_memory_stats()

    def predict(self, arrays):
        torch = self.torch
        batch = torch.from_numpy(arrays).to(self.device)
        with torch.inference_mode():
            # FP32 by default: export/retry values are independent of batch padding.
            logits = self.model(batch)
            if not torch.isfinite(logits).all():
                raise ValueError('Non-finite model output')
            scores = logits.softmax(1).cpu().numpy()
        return scores

    def memory_mib(self):
        return self.torch.cuda.max_memory_allocated()/1024**2 if self.device=='cuda' else None

    def close(self):
        self.model=None
        if self.device=='cuda':self.torch.cuda.empty_cache()
