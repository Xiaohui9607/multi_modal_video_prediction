import os
import torch
from torch import nn
from networks import network
from data.data import build_dataloader_CY101
from torch.nn import functional as F

def peak_signal_to_noise_ratio(true, pred):
  """Image quality metric based on maximal signal power vs. power of the noise.

  Args:
    true: the ground truth image.
    pred: the predicted image.
  Returns:
    peak signal to noise ratio (PSNR)
  """
  return 10.0 * torch.log(torch.tensor(1.0) / F.mse_loss(true, pred)) / torch.log(torch.tensor(10.0))


class Model():
    def __init__(self, opt):
        self.opt = opt
        self.device = self.opt.device

        train_dataloader, valid_dataloader = build_dataloader_CY101(opt)
        self.dataloader = {'train': train_dataloader, 'valid': valid_dataloader}

        self.net = network(self.opt.channels, self.opt.height, self.opt.width, -1, self.opt.schedsamp_k,
                               self.opt.use_state, self.opt.num_masks, self.opt.model=='STP', self.opt.model=='CDNA', self.opt.model=='DNA', self.opt.context_frames)
        self.net.to(self.device)
        self.mse_loss = nn.MSELoss()
        self.w_state = 1e-4
        if self.opt.pretrained_model:
            self.load_weight()
        self.optimizer = torch.optim.Adam(self.net.parameters(), self.opt.learning_rate)

    def train_epoch(self, epoch):
        print("--------------------start training epoch %2d--------------------" % epoch)
        for iter_, images in enumerate(self.dataloader['train']):
            self.net.zero_grad()
            actions = torch.zeros(images.shape[0], images.shape[1], 5).to(self.device)
            states = torch.zeros_like(actions).to(self.device)
            images = images.permute([1, 0, 2, 3, 4]).unbind(0)
            actions = actions.permute([1, 0, 2]).unbind(0)
            states = states.permute([1, 0, 2]).unbind(0)

            gen_images, _ = self.net(images, actions, states[0])

            loss, psnr = 0.0, 0.0

            for i, (image, gen_image) in enumerate(zip(images[self.opt.context_frames:], gen_images[self.opt.context_frames-1:])):
                recon_loss = self.mse_loss(image, gen_image)
                psnr_i = peak_signal_to_noise_ratio(image, gen_image)
                loss += recon_loss
                psnr += psnr_i

            loss /= torch.tensor(self.opt.sequence_length - self.opt.context_frames)
            loss.backward()
            self.optimizer.step()

            if iter_ % self.opt.print_interval == 0:
                print("training epoch: %3d, iterations: %3d/%3d loss: %6f" %
                      (epoch, iter_, len(self.dataloader['train'].dataset)//self.opt.batch_size, loss))

            self.net.iter_num += 1

    def train(self):
        for epoch_i in range(0, self.opt.epochs):
            self.train_epoch(epoch_i)
            self.evaluate(epoch_i)
            self.save_weight(epoch_i)

    def evaluate(self, epoch):
        with torch.no_grad():
            recon_loss, state_loss = 0.0, 0.0
            for iter_, images in enumerate(self.dataloader['valid']):
                actions = torch.zeros(images.shape[0], images.shape[1], 5).to(self.device)
                states = torch.zeros_like(actions).to(self.device)
                images = images.permute([1, 0, 2, 3, 4]).unbind(0)

                actions = actions.permute([1, 0, 2]).unbind(0)
                states = states.permute([1, 0, 2]).unbind(0)

                gen_images, _ = self.net(images, actions, states[0])
                for i, (image, gen_image) in enumerate(
                        zip(images[self.opt.context_frames:], gen_images[self.opt.context_frames - 1:])):
                    recon_loss += self.mse_loss(image, gen_image)

            recon_loss /= (torch.tensor(self.opt.sequence_length - self.opt.context_frames) * len(self.dataloader['valid'].dataset)/self.opt.batch_size)

            print("evaluation epoch: %3d, recon_loss: %6f, state_loss: %6f" % (epoch, recon_loss, state_loss))

    def save_weight(self, epoch):
        torch.save(self.net.state_dict(), os.path.join(self.opt.output_dir, "net_epoch_%d.pth" % epoch))

    def load_weight(self, path=None):
        if path:
            self.net.load_state_dict(torch.load(path))
        elif self.opt.pretrained_model:
            self.net.load_state_dict(torch.load(self.opt.pretrained_model))

