# test-stubs

Fake `nvidia-smi`, `mpirun`, and `*_perf` binaries that emit realistic
output, for exercising dgx-vitals.py without DGX hardware. Put this
folder on the PATH and run the tool in --local mode. Environment knobs:
NCCL_WRONG, NCCL_OOB (all-reduce), NCCL_WRONG_GATHER, NCCL_OOB_GATHER.
