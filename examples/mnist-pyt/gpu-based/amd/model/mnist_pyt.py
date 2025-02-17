############################################################################
## (C)Copyright 2021-2023 Hewlett Packard Enterprise Development LP
## Licensed under the Apache License, Version 2.0 (the "License"); you may
## not use this file except in compliance with the License. You may obtain
## a copy of the License at
##
##    http://www.apache.org/licenses/LICENSE-2.0
##
## Unless required by applicable law or agreed to in writing, software
## distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
## WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
## License for the specific language governing permissions and limitations
## under the License.
############################################################################

import datetime
import numpy as np
import os
from swarmlearning.pyt import SwarmCallback
from torchvision import datasets, transforms
import time
import torch 
import torch.nn as nn
import torch.nn.functional as F
import torch.optim as optim

import pdb
default_max_epochs = 5
default_min_peers = 2
# maxEpochs = 2
trainPrint = True
# tell swarm after how many batches
# should it Sync. We are not doing 
# adaptiveRV here, its a simple and quick demo run
swSyncInterval = 256

import pyamdgpuinfo


class mnistNet(nn.Module):
    def __init__(self):
        super(mnistNet, self).__init__()
        self.dense = nn.Linear(784, 512)
        self.dropout = nn.Dropout(0.2)
        self.dense1 = nn.Linear(512, 10)
        
    def forward(self, x):
        x = torch.flatten(x, 1)        
        x = self.dense(x)
        x = F.relu(x)
        x = self.dropout(x)
        x = self.dense1(x)
        output = F.log_softmax(x, dim=1)
        return output
        
def loadData():
    train_ds = datasets.MNIST('../data', train=True, download=True, transform=transforms.ToTensor())
    test_ds = datasets.MNIST('../data', train=False, transform=transforms.ToTensor())
    
    return train_ds, test_ds
    
def doTrainBatch(model,device,trainLoader,optimizer,epoch,swarmCallback):
    model.train()
    for batchIdx, (data, target) in enumerate(trainLoader):
        data, target = data.to(device), target.to(device)
        optimizer.zero_grad()
        output = model(data)
        loss = F.nll_loss(output, target)
        loss.backward()
        optimizer.step()
        if trainPrint and batchIdx % 100 == 0:
            print('Train Epoch: {} [{}/{} ({:.0f}%)]\tLoss: {:.6f}'.format(
                  epoch, batchIdx * len(data), len(trainLoader.dataset),
                  100. * batchIdx / len(trainLoader), loss.item()))
        # Swarm Learning Interface
        if swarmCallback is not None:
            swarmCallback.on_batch_end()        

def test(model, device, testLoader):
    model.eval()
    testLoss = 0
    correct = 0
    with torch.no_grad():
        for data, target in testLoader:
            data, target = data.to(device), target.to(device)
            output = model(data)
            testLoss += F.nll_loss(output, target, reduction='sum').item()  # sum up batch loss
            pred = output.argmax(dim=1, keepdim=True)  # get the index of the max log-probability
            correct += pred.eq(target.view_as(pred)).sum().item()

    testLoss /= len(testLoader.dataset)

    print('\nTest set: Average loss: {:.4f}, Accuracy: {}/{} ({:.0f}%)\n'.format(
        testLoss, correct, len(testLoader.dataset),
        100. * correct / len(testLoader.dataset)))    

def main():
    scratchDir = os.getenv('SCRATCH_DIR', '/platform/scratch')
    modelDir = os.getenv('MODEL_DIR', '/platform/model')
    max_epochs = int(os.getenv('MAX_EPOCHS', str(default_max_epochs)))
    min_peers = int(os.getenv('MIN_PEERS', str(default_min_peers)))
    batchSz = 25000
    trainDs, testDs = loadData()
    useCuda = torch.cuda.is_available()
    
    if useCuda:
        print("Cuda is accessable")
    else:
        print("Cuda is not accessable")
        
        
    n_devices = pyamdgpuinfo.detect_gpus()
    print("No of GPU devices available in this host:", n_devices)
    gpusDetected = False
    gpu = None
    if (n_devices > 0):
        gpusDetected = True
    
    if (gpusDetected):
        gpuDeviceNo = os.getenv('HIP_VISIBLE_DEVICES' ,0)
        print("Gpu id requested for local training :", gpuDeviceNo)
        gpu = pyamdgpuinfo.get_gpu(int(gpuDeviceNo)) 


    device = torch.device("cuda" if useCuda else "cpu")  
    model = mnistNet().to(device)
    model_name = 'mnist_pyt'
    opt = optim.Adam(model.parameters())
    trainLoader = torch.utils.data.DataLoader(trainDs, batch_size=batchSz)
    testLoader = torch.utils.data.DataLoader(testDs, batch_size=batchSz)
    
    # Create Swarm callback
    swarmCallback = None
    swarmCallback = SwarmCallback(syncFrequency=swSyncInterval,
                                  minPeers=min_peers,
                                  useAdaptiveSync=False,
                                  adsValData=testDs,
                                  adsValBatchSize=batchSz,
                                  model=model)
    # initalize swarmCallback and do first sync 
    swarmCallback.on_train_begin()
        
    for epoch in range(1, max_epochs + 1):
        doTrainBatch(model,device,trainLoader,opt,epoch,swarmCallback)      
        test(model,device,testLoader)
        swarmCallback.on_epoch_end(epoch)
        if gpu is not None: 
            print(" Epoch {} : VRAM usage {} ".format(epoch, gpu.query_vram_usage()))
            # When no GPUs are requested, observed aprrox 2 MB showed by Vram usage
            # so considering above 100 MB usage of Vram as GPUs accessed. 
            amdGpuMemoryUsedin100MBs = (gpu.query_vram_usage()/800000000)
            print(" Gpus use in units of 100Mbs :", amdGpuMemoryUsedin100MBs)
            VAL = (amdGpuMemoryUsedin100MBs > 1.0)
            print (" AMD GPUs used : ", VAL)
            print(" Epoch End callback done")


    # handles what to do when training ends        
    swarmCallback.on_train_end()

    # Save model and weights
    saved_model_path = os.path.join(scratchDir, model_name, 'saved_model.pt')
    # Pytorch model save function expects the directory to be created before hand.
    os.makedirs(scratchDir, exist_ok=True)
    os.makedirs(os.path.join(scratchDir, model_name), exist_ok=True)
    torch.save(model, saved_model_path)
    print('Saved the trained model!')
 
if __name__ == '__main__':
  main()
