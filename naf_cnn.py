import sys

import torch
import torch.nn as nn
from torch.optim import Adam
from torch.autograd import Variable
import torch.nn.functional as F

MSELoss = nn.MSELoss()


def soft_update(target, source, tau):
    for target_param, param in zip(target.parameters(), source.parameters()):
        target_param.data.copy_(target_param.data * (1.0 - tau) + param.data * tau)


def hard_update(target, source):
    for target_param, param in zip(target.parameters(), source.parameters()):
        target_param.data.copy_(param.data)


class Policy(nn.Module):

    def __init__(self, hidden_size, num_inputs, action_space):
        super(Policy, self).__init__()
        self.action_space = action_space
        num_outputs = action_space.shape[0]

        self.conv1 = nn.Sequential(
            nn.Conv1d(
                in_channels=2,
                out_channels=16,
                kernel_size=4,
                stride=1,
                padding=1,
            ),
            nn.ReLU(),
            nn.MaxPool1d(kernel_size=2),
        )
        self.out1 = nn.Linear(48, 16)

        self.conv2 = nn.Sequential(
            nn.Conv1d(
                in_channels=2,
                out_channels=16,
                kernel_size=4,
                stride=1,
                padding=1,
            ),
            nn.ReLU(),
            nn.MaxPool1d(kernel_size=2),
        )
        self.out2 = nn.Linear(48, 16)

        self.conv3 = nn.Sequential(
            nn.Conv1d(
                in_channels=2,
                out_channels=16,
                kernel_size=4,
                stride=1,
                padding=1,
            ),
            nn.ReLU(),
            nn.MaxPool1d(kernel_size=2),
        )
        self.out3 = nn.Linear(48, 16)

        self.bn0 = nn.BatchNorm1d(num_inputs)
        self.bn0.weight.data.fill_(1)
        self.bn0.bias.data.fill_(0)

        self.linear1 = nn.Linear(num_inputs, hidden_size)
        self.bn1 = nn.BatchNorm1d(hidden_size)
        self.bn1.weight.data.fill_(1)
        self.bn1.bias.data.fill_(0)

        self.linear2 = nn.Linear(hidden_size, hidden_size)
        self.bn2 = nn.BatchNorm1d(hidden_size)
        self.bn2.weight.data.fill_(1)
        self.bn2.bias.data.fill_(0)

        self.V = nn.Linear(hidden_size, 1)
        self.V.weight.data.mul_(0.1)
        self.V.bias.data.mul_(0.1)

        self.mu = nn.Linear(hidden_size, num_outputs)
        self.mu.weight.data.mul_(0.1)
        self.mu.bias.data.mul_(0.1)

        self.L = nn.Linear(hidden_size, num_outputs ** 2)
        self.L.weight.data.mul_(0.1)
        self.L.bias.data.mul_(0.1)

        self.tril_mask = Variable(torch.tril(torch.ones(
            num_outputs, num_outputs), diagonal=-1).unsqueeze(0))
        self.diag_mask = Variable(torch.diag(torch.diag(
            torch.ones(num_outputs, num_outputs))).unsqueeze(0))

    def forward(self, inputs):
        inputs, u = inputs
        cnn1 = inputs[:, 0:8].contiguous()
        cnn1 = cnn1.view(1, 2, 8)
        cnn1 = self.conv1(cnn1)
        cnn1 = cnn1.view(cnn1.size(0), -1)
        cnn1 = self.out1(cnn1)
        cnn1 = cnn1.view(2, 8)

        cnn2 = inputs[:, 8:16].contiguous()
        cnn2 = cnn2.view(1, 2, 8)
        cnn2 = self.conv2(cnn2)
        cnn2 = cnn2.view(cnn2.size(0), -1)
        cnn2 = self.out2(cnn2)
        cnn2 = cnn2.view(2, 8)

        cnn3 = inputs[:, 16:24].contiguous()
        cnn3 = cnn3.view(1, 2, 8)
        cnn3 = self.conv3(cnn3)
        cnn3 = cnn3.view(cnn3.size(0), -1)
        cnn3 = self.out3(cnn3)
        cnn3 = cnn3.view(2, 8)

        x = torch.cat((cnn1, cnn2), 1)
        x = torch.cat((x, cnn3), 1)
        x = torch.cat((x, inputs[:, 24:]), 1)

        x = self.bn0(x)
        x = F.tanh(self.linear1(x))
        x = F.tanh(self.linear2(x))

        V = self.V(x)
        mu = F.tanh(self.mu(x))

        Q = None
        if u is not None:
            num_outputs = mu.size(1)
            L = self.L(x).view(-1, num_outputs, num_outputs)
            L = L * \
                self.tril_mask.expand_as(
                    L) + torch.exp(L) * self.diag_mask.expand_as(L)
            P = torch.bmm(L, L.transpose(2, 1))

            u_mu = (u - mu).unsqueeze(2)
            A = -0.5 * \
                torch.bmm(torch.bmm(u_mu.transpose(2, 1), P), u_mu)[:, :, 0]

            Q = A + V

        return mu, Q, V


class NAF_CNN:

    def __init__(self, gamma, tau, hidden_size, num_inputs, action_space):
        self.action_space = action_space
        self.num_inputs = num_inputs

        self.model = Policy(hidden_size, num_inputs, action_space)
        self.target_model = Policy(hidden_size, num_inputs, action_space)
        self.optimizer = Adam(self.model.parameters(), lr=1e-3)

        self.gamma = gamma
        self.tau = tau

        hard_update(self.target_model, self.model)

    def select_action(self, state, exploration=None):
        self.model.eval()
        mu, _, _ = self.model((Variable(state, volatile=True), None))
        self.model.train()
        mu = mu.data
        if exploration is not None:
            mu += torch.Tensor(exploration.noise())

        return mu.clamp(1, 4)

    def update_parameters(self, batch):
        state_batch = Variable(torch.cat(batch.state))
        next_state_batch = Variable(torch.cat(batch.next_state), volatile=True)
        action_batch = Variable(torch.cat(batch.action))
        reward_batch = Variable(torch.cat(batch.reward))
        mask_batch = Variable(torch.cat(batch.mask))

        _, _, next_state_values = self.target_model((next_state_batch, None))

        reward_batch = (torch.unsqueeze(reward_batch, 1))
        expected_state_action_values = reward_batch + (next_state_values * self.gamma)

        _, state_action_values, _ = self.model((state_batch, action_batch))

        loss = MSELoss(state_action_values, expected_state_action_values)

        self.optimizer.zero_grad()
        loss.backward()
        torch.nn.utils.clip_grad_norm(self.model.parameters(), 1)
        self.optimizer.step()

        soft_update(self.target_model, self.model, self.tau)
