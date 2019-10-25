from __future__ import print_function, division
import sys
import matplotlib.pyplot as plt
import numpy as np
import pickle
import glob
from music21 import converter, instrument, note, chord, stream
import torch
import torch.nn as nn
import torch.optim as optim
import torch.functional as F
import os

GLOBAL_SEQUENCE_LENGTH = 100

def get_notes(n_notes=3):
    """ Get all the notes and chords from the midi files """
    notes = []
    # import pdb; pdb.set_trace()
    for ii, file in enumerate(glob.glob("data/maestro-v2.0.0/2017/*.midi")):
        if ii >= n_notes:
            break
        pickle_file_name = file[:-4] + 'pkl'

        if os.path.isfile(pickle_file_name):
            print(f'Reading parsed file: {pickle_file_name}')
            with open(pickle_file_name, 'rb') as handle:
                midi = pickle.load(handle)
        else:
            midi = converter.parse(file)

            with open(pickle_file_name, 'wb') as handle:
                print(f'writing parsed file: {pickle_file_name}')
                unserialized_data = pickle.dump(midi, 
                    handle, 
                    protocol=pickle.HIGHEST_PROTOCOL
                    )


        print("Parsing %s" % file)

        notes_to_parse = None
        try: # file has instrument parts
            s2 = instrument.partitionByInstrument(midi)
            notes_to_parse = s2.parts[0].recurse() 
        except: # file has notes in a flat structure
            notes_to_parse = midi.flat.notes
            
        for element in notes_to_parse:
            if isinstance(element, note.Note):
                notes.append(str(element.pitch))
            elif isinstance(element, chord.Chord):
                notes.append('.'.join(str(n) for n in element.normalOrder))
        
    return notes

def prepare_sequences(notes, n_vocab):
    """ Prepare the sequences used by the Neural Network """
    sequence_length = GLOBAL_SEQUENCE_LENGTH

    # Get all pitch names
    pitchnames = sorted(set(item for item in notes))

    # Create a dictionary to map pitches to integers
    note_to_int = dict((note, number) for number, note in enumerate(pitchnames))

    network_input = []
    

    # create input sequences and the corresponding outputs
    for i in range(0, len(notes) - sequence_length, 1):
        sequence_in = notes[i:i + sequence_length]
        sequence_out = notes[i + sequence_length]
        network_input.append([note_to_int[char] for char in sequence_in])
        

    n_patterns = len(network_input)

    # Reshape the input into a format compatible with LSTM layers
    # import pdb; pdb.set_trace()
    # network_input = np.reshape(network_input, (n_patterns, sequence_length, 1))
    network_input = np.array(network_input)
    # Normalize input between -1 and 1
    network_input = (network_input - float(n_vocab)/2) / (float(n_vocab)/2)
    # import pdb; pdb.set_trace()

    return network_input

def generate_notes(model, network_input, n_vocab):
    """ Generate notes from the neural network based on a sequence of notes """
    # pick a random sequence from the input as a starting point for the prediction
    start = np.random.randint(0, len(network_input)-1)
    
    # Get pitch names and store in a dictionary
    pitchnames = sorted(set(item for item in notes))
    int_to_note = dict((number, note) for number, note in enumerate(pitchnames))

    pattern = network_input[start]
    prediction_output = []

    # generate 500 notes
    for note_index in range(500):
        prediction_input = np.reshape(pattern, (1, len(pattern), 1))
        prediction_input = prediction_input / float(n_vocab)

        prediction = model.predict(prediction_input, verbose=0)

        index = np.argmax(prediction)
        result = int_to_note[index]
        prediction_output.append(result)
        
        pattern = np.append(pattern,index)
        #pattern.append(index)
        pattern = pattern[1:len(pattern)]

    return prediction_output
  
def create_midi(prediction_output, filename):
    """ convert the output from the prediction to notes and create a midi file
        from the notes """
    offset = 0
    output_notes = []

    # create note and chord objects based on the values generated by the model
    for item in prediction_output:
        pattern = item#[0]
        # pattern is a chord
        if ('.' in pattern) or pattern.isdigit():
            # import pdb; pdb.set_trace()
            notes_in_chord = pattern.split('.')
            notes = []
            for current_note in notes_in_chord:
                new_note = note.Note(int(current_note))
                new_note.storedInstrument = instrument.Piano()
                notes.append(new_note)
            new_chord = chord.Chord(notes)
            new_chord.offset = offset
            output_notes.append(new_chord)
        # pattern is a note
        else:
            new_note = note.Note(pattern)
            new_note.offset = offset
            new_note.storedInstrument = instrument.Piano()
            output_notes.append(new_note)

        # increase offset each iteration so that notes do not stack
        offset += 0.5

    midi_stream = stream.Stream(output_notes)
    midi_stream.write('midi', fp='{}.mid'.format(filename))


class Discriminator(nn.Module):
    def __init__(self, n_units):
        super(Discriminator, self).__init__()
        self.sequence_length = n_units
        self.LSTM_hidden_dim = 512

        self.LSTM = nn.LSTM(
            input_size=self.sequence_length, 
            hidden_size=self.LSTM_hidden_dim,
            num_layers=2
            )

        self.linear_layers = nn.Sequential(
            nn.Linear(512, 512),
            nn.LeakyReLU(),
            nn.Linear(512, 256),
            nn.LeakyReLU(),
            nn.Linear(256, 1),
            nn.Sigmoid()
            )
    
    def forward(self, x):

        # import pdb; pdb.set_trace()
        hidden_1 = torch.zeros(2, 128, 512) # magic nuuuuuumbers 0.0
        hidden_2 = torch.zeros(2, 128, 512) # num_layers, batch_size, hidden_dimension
        out, (hidden_1, hidden_2) = self.LSTM(x, (hidden_1, hidden_2))

        x = self.linear_layers(out)
        return x


class Generator(nn.Module):
    def __init__(self, n_units):
        super(Generator, self).__init__()
        self.sequence_length = n_units
        
        self.first_linear_layers = nn.Sequential(
            nn.Linear(self.sequence_length, self.sequence_length),
            nn.ReLU(),
            nn.Linear(self.sequence_length, self.sequence_length),
            nn.Tanh()
        )
        self.LSTM = nn.LSTM(
            input_size = self.sequence_length,
            hidden_size = self.sequence_length,
            num_layers=1
        )

        self.second_linear_layers = nn.Sequential(
            nn.Linear(self.sequence_length, 256),
            nn.ReLU(),
            # nn.BatchNorm1d(num_features=256), # TODO: Make batchnorm work at some time
            nn.Linear(256, 1024),
            nn.ReLU(),
            # nn.BatchNorm1d(num_features=1024),
            nn.Linear(1024, self.sequence_length),
            nn.Tanh()
        )
        
    def forward(self, x):
        x = torch.stack([x])
        # import pdb; pdb.set_trace()
        hidden = torch.zeros(1, x.shape[1], self.sequence_length)
        cell_init_state = hidden = torch.zeros(1, x.shape[1], self.sequence_length)
        x, (hidden, cell_init_state) = self.LSTM(x, (hidden, cell_init_state))
        x = self.second_linear_layers(x)

        # import pdb; pdb.set_trace()
        return x
        
class torchGAN():

    def __init__(self, n_units):
        self.sequence_length = n_units
        self.latent_dim = 1000
        learning_rate = 0.0001
        # create discriminator and generator 
        self.discriminator = Discriminator(self.sequence_length) # LSTM + fc
        self.generator = Generator(self.sequence_length) # fc
        self.optimizer_generator = optim.Adam(self.generator.parameters(), lr=learning_rate)
        self.optimizer_discriminator = optim.Adam(self.discriminator.parameters(), lr=learning_rate)
    
    def train(self, n_epochs, batch_size=128, sample_interval=50):
        notes = get_notes(n_notes=10)
        n_vocab = len(set(notes))
        X_train = prepare_sequences(notes, n_vocab)
        # import pdb; pdb.set_trace()
        # Adversarial ground truths
        label_real = torch.tensor(np.ones((batch_size, 1))).float()
        label_fake = torch.tensor(np.zeros((batch_size, 1))).float()
        loss_func = nn.BCELoss()

        discriminator_epoch_loss = []
        generator_epoch_loss = []
        
        gen_loss_per_batch_total = []
        disc_loss_per_batch_total = []


        # Training the model
        for i_epoch in range(n_epochs):
            gen_loss_per_batch = []
            disc_loss_per_batch = []
            n_batches_in_epoch = X_train.shape[0] // batch_size
            for i_batch in range(n_batches_in_epoch):

                sys.stdout.write(f"\rat batch: {i_batch} / {n_batches_in_epoch} in epoch {i_epoch + 1} / {n_epochs}")
                sys.stdout.flush()
                # ------------------------------------
                # Training the discriminator
                # Select a random batch of note sequences
                self.optimizer_discriminator.zero_grad()

                idx = np.random.randint(0, X_train.shape[0], batch_size)
                real_seqs = X_train[idx] # batch of real music sequences 
                real_seqs = torch.tensor(real_seqs)
                real_seqs = torch.stack([real_seqs]).float()
                
                # train on real data
                real_d = self.discriminator(real_seqs)
                loss_disc = loss_func(real_d[0], label_real)

                # train on fake data 
                noise = torch.tensor(np.random.normal(0, 1, (batch_size, self.sequence_length))).float()
                # import pdb; pdb.set_trace()
                with torch.no_grad():
                    fake_seqs = self.generator(noise)
                # fake_seqs = torch.stack([fake_seqs]).float()
                # import pdb; pdb.set_trace()
                fake_d = self.discriminator(fake_seqs)
                loss_disc += loss_func(fake_d[0], label_fake)  # add losses and update
                loss_disc.backward()
                self.optimizer_discriminator.step()

                # ------------------------------------
                # Training the generator           
                self.optimizer_discriminator.zero_grad()
                self.optimizer_generator.zero_grad()
                
                noise = torch.tensor(np.random.normal(0, 1, (batch_size, self.sequence_length))).float()

                fake_seqs = self.generator(noise)
                # fake_seqs = torch.stack([fake_seqs]).float()
                # with torch.no_grad():
                fake_d = self.discriminator(fake_seqs)
                
                loss_gen = loss_func(fake_d[0], label_real)
                loss_gen.backward()
                self.optimizer_generator.step() # what is backpropped? :S
                # import pdb; pdb.set_trace()
                disc_loss_per_batch.append(loss_disc)
                gen_loss_per_batch.append(loss_gen)

            disc_loss_per_batch_total   += disc_loss_per_batch
            gen_loss_per_batch_total    += gen_loss_per_batch
            
            avg_gen_loss    = sum(gen_loss_per_batch) / len(gen_loss_per_batch)
            avg_disc_loss   = sum(disc_loss_per_batch) / len(disc_loss_per_batch)
            
            generator_epoch_loss.append(avg_gen_loss)
            discriminator_epoch_loss.append(avg_disc_loss)
            
            print(
                '\ndiscr: loss %.3f\t gener: loss = %.3f\t epoch: %d / %d' % (\
                    avg_disc_loss, avg_gen_loss, (i_epoch + 1), n_epochs)
            )
            
        
        self.generate(input_notes=notes)
        self.draw_loss(disc_loss_per_batch_total, gen_loss_per_batch_total)


    def generate(self, input_notes):
        # Get pitch names and store in a dictionary
        notes = input_notes
        n_vocab = len(set(notes))
        pitchnames = sorted(set(item for item in notes))
        int_to_note = dict((number, note) for number, note in enumerate(pitchnames))
        
        # Use random noise to generate sequences
        noise = torch.tensor(np.random.normal(0, 1, (1, self.sequence_length))).float()
        predictions = self.generator(noise)
        
        #network_input = (network_input - float(n_vocab)/2) / (float(n_vocab)/2)
        pred_notes = [x * (n_vocab - 1) / 2 + n_vocab / 2 for x in predictions[0]][0]
        #pred_notes = [(x + 1)*189//2 for x in predictions[0]] # 242+242
        # import pdb; pdb.set_trace()
        pred_notes = [int_to_note[int(x)] for x in pred_notes]
        print(f'pred_notes \n {pred_notes}')
        print(f'prediction \n {predictions}')
        create_midi(pred_notes, 'gan_final')
    
    def sequence_to_midi(self, file_name, input_notes, sequence):
        notes = input_notes
        n_vocab = len(set(notes))
        pitchnames = sorted(set(item for item in notes))
        int_to_note = dict((number, note) for number, note in enumerate(pitchnames))

        pred_notes = [x * (n_vocab - 1) / 2 + (n_vocab - 1) / 2 for x in sequence[0]]
        #pred_notes = [(x + 1)*189//2 for x in predictions[0]] # 242+242
        import pdb; pdb.set_trace()

        pred_notes = [int_to_note[int(x)] for x in pred_notes]
        
        create_midi(pred_notes, file_name)

    def draw_loss(self, discriminator_epoch_loss, generator_epoch_loss):
        epochs = range(len(discriminator_epoch_loss))
        
        fig, ax = plt.subplots(1,1)
        disc_plot = ax.plot(epochs, discriminator_epoch_loss, label='Discriminator')
        gen_plot = ax.plot(epochs, generator_epoch_loss, label='Generator')
        ax.legend()
        ax.grid()
        ax.set_xlabel('batch')
        ax.set_ylabel('loss')
        ax.set_title('Loss versus batch')
        plt.savefig('plot_of_loss_1.png')
        #plt.show()
        # plt.draw()
        # plt.pause(0.01)

        print(' ')
    
    
if __name__ == '__main__':

    gan = torchGAN(n_units=GLOBAL_SEQUENCE_LENGTH)
    gan.train(1000)
    # notes = get_notes()
    # gan.generate(input_notes=notes)   
    # gan.train(n_epochs=3)

    print('done')
    

