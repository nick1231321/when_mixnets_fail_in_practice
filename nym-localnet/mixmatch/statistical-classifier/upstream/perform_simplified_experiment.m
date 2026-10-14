
% The following parameters are the same for all the experiments:
max_sequences_length = 100;
sequences_count = 1000;
% lambda is the MixNet nodes delay parameter: Delay_mixnetnode ~ exp(lambda)
lambda = 1;


if ~exist('Si_matrix_mu1', 'var')
  % We start by generating all the inbound and outbound time sequences assuming mu = lambda, where mu is the source parameter: Delay_source ~ exp(mu)
  % They can be later modified to obtain the equivalent inbound and outbound time sequences for different mu
  fprintf(1, 'Generating %i inbound times sequences of length %i assuming mu = lambda\n', sequences_count, max_sequences_length); fflush(1);
  fprintf(1, 'These sequences will be used in all the experiments, since modifying the source rate by a factor x is equivalent to multiply these inbound times by 1/x\n');
  Si_matrix_mu1 = zeros(max_sequences_length, sequences_count);
  for k=1:sequences_count
    Si_matrix_mu1(:,k) = generate_input_times_sequence(max_sequences_length, lambda);
    fprintf(1, '\rProgress: %.2f%%', 100*k/sequences_count); fflush(1);
  endfor
  fprintf(1, '. Done!\n');
  fprintf(1, 'Generating outbound times sequences by applying MixNet delays\n'); fflush(1);
  fprintf(1, 'Again, these outbound times sequences will be used in all the experiments, since we can compute the MixNet delay by substracting the corresponding inbound times, and then obtain the new outbound times by simply adding the modified inbound times to the MixNet delay, that remains the same. This way we avoid generating new MixNet delays.\n');
endif

if ~exist('So_matrix_mu1', 'var')
  So_matrix_mu1 = zeros(size(Si_matrix_mu1));
  for k=1:sequences_count
    So_matrix_mu1(:,k) = apply_mixnet_delay(Si_matrix_mu1(:,k), lambda);
    fprintf(1, '\rProgress: %.2f%%', 100*k/sequences_count); fflush(1);
  endfor
  fprintf(1, '. Done!\n');
endif

% mu_factor is the factor that multiplies the MixNet parameter lambda, i.e., mu = lambda*mu_factor.
% If mu_factor is bigger than 1 then mu is bigger than lambda, i.e., the source delays are smaller than the mixnet nodes delays.
mu_factor_vector = [0.2 0.4 0.6 0.8 1.0 1.2 1.4 1.6 1.8 2.0];

% This vector is the lengths of the sequences explored during the experiments.
sequences_length_vector = 1:max_sequences_length;

% Each mu and each sequence length will have its corresponging performance indicators.
FAR = zeros(length(mu_factor_vector), length(sequences_length_vector));
FRR = zeros(length(mu_factor_vector), length(sequences_length_vector));
EER = zeros(length(mu_factor_vector), length(sequences_length_vector));

% Other statistics
true_matches_loglikelihoods_mean_matrix = zeros(length(mu_factor_vector), length(sequences_length_vector));
true_matches_loglikelihoods_std_matrix = zeros(length(mu_factor_vector), length(sequences_length_vector));
inf_false_scores_count_matrix = zeros(length(mu_factor_vector), length(sequences_length_vector));
non_inf_false_scores_mean_matrix = zeros(length(mu_factor_vector), length(sequences_length_vector));
non_inf_false_scores_std_matrix = zeros(length(mu_factor_vector), length(sequences_length_vector));

figure;

for mu_factor_index = 1:length(mu_factor_vector)
  
  mu = mu_factor_vector(mu_factor_index);
  
  fprintf(1, 'Performing experiment for mu = %.1f\n', mu);
  fprintf(1, '==================================\n');

  fprintf(1, '\tTransforming the inbound times from the original ones... ');
  Si_matrix = Si_matrix_mu1/mu;
  fprintf(1, 'Done!\n');
  fprintf(1, '\tTransforming the outbound times from the original ones... ');
  So_matrix = So_matrix_mu1 - Si_matrix_mu1 + Si_matrix;
  fprintf(1, 'Done!\n');
  
  fprintf(1, '\tComputing maximum loglikelihoods\n');
  loglikelihood_matrix = zeros(sequences_count, sequences_count, max_sequences_length);
  for Si_index = 1:sequences_count
    for So_index = 1:sequences_count
      loglikelihood_matrix(Si_index, So_index, :) = ...
      simplified_sequences_match_loglikelihood(Si_matrix(:,Si_index), So_matrix(:,So_index), lambda, true);
      fprintf(1, '\r\tProgress: %.2f%%', 100*((Si_index - 1) + So_index/sequences_count)/sequences_count); fflush(1);
    endfor
  endfor
  fprintf(1, ' Done!\n\n');
  
  for sequences_length_index = 1:length(sequences_length_vector)

    sequences_length = sequences_length_vector(sequences_length_index);
    
    fprintf(1, '\n\tLength %i sequences experiment\n', sequences_length);
    fprintf(1, '\t-----------------------------\n');
    
    true_matches_loglikelihoods_vector = [];
    false_matches_loglikelihoods_vector = [];

    true_matches_count = sequences_count;
    false_matches_count = sequences_count*(sequences_count - 1);
    true_matches_loglikelihoods_vector = zeros(true_matches_count, 1);
    false_matches_loglikelihoods_vector = zeros(false_matches_count, 1);
    
    for Si_index = 1:sequences_count
      for So_index = 1:sequences_count
	if Si_index == So_index
	  true_matches_loglikelihoods_vector(Si_index) = mean(loglikelihood_matrix(Si_index, So_index, 1:sequences_length));
	else
	  false_matches_loglikelihoods_vector((Si_index - 1)*(sequences_count - 1) + So_index - (So_index > Si_index)) = mean(loglikelihood_matrix(Si_index, So_index, 1:sequences_length));
	endif
	fprintf(1, '\r\tProgress: %.2f%%', 100*((Si_index - 1) + So_index/sequences_count)/sequences_count); fflush(1);
      endfor
    endfor
    
    fprintf(1, '. Finished!\n');
    
    % fprintf(1, '\tSubstituting all -Inf from false matches loglikelihoods by a constant slightly less than the most negative non infinite loglikelihood (to avoid numerical problems)... ');
    inf_false_scores_count = sum(false_matches_loglikelihoods_vector == -Inf);
    % fprintf(1, '\tPercentage of -Inf false scores: %.2f%%\n', 100*inf_false_scores_count/length(false_matches_loglikelihoods_vector));
    non_inf_false_scores_mean = mean(false_matches_loglikelihoods_vector(false_matches_loglikelihoods_vector ~= -Inf));
    non_inf_false_scores_std = std(false_matches_loglikelihoods_vector(false_matches_loglikelihoods_vector ~= -Inf));
    false_matches_loglikelihoods_vector(false_matches_loglikelihoods_vector == -Inf) = min(false_matches_loglikelihoods_vector(false_matches_loglikelihoods_vector ~= -Inf)) - eps;
    % In case there is still -Inf in the negative loglikelihoods, this means that all negative scores are -Inf, so we can substitute them by the minimum positive score - eps
    false_matches_loglikelihoods_vector(false_matches_loglikelihoods_vector == -Inf) = min(true_matches_loglikelihoods_vector) - eps;
    % fprintf(1, 'Done!\n\n');
    % Computing and printing performance
    [thr, FAR(mu_factor_index, sequences_length_index), FRR(mu_factor_index, sequences_length_index), EER(mu_factor_index, sequences_length_index)] = ...
    getThresholdEER(true_matches_loglikelihoods_vector, false_matches_loglikelihoods_vector);
    fprintf(1, '\tMatching performance of experiment with sequences of length %i:\n', sequences_length);
    fprintf(1, '\t\tFalse Match Rate:     %.4f%%\n', 100*FAR(mu_factor_index, sequences_length_index));
    fprintf(1, '\t\tFalse Non Match Rate: %.4f%%\n', 100*FRR(mu_factor_index, sequences_length_index));
    fprintf(1, '\t\tBalanced Accuracy:    %.4f%%\n', 100*(1 - EER(mu_factor_index, sequences_length_index)));
    fprintf(1, '\t\t------------------------------\n');
    fprintf(1, '\t\tTrue scores average:  %.4f\n', mean(true_matches_loglikelihoods_vector))
    fprintf(1, '\t\tTrue scores std dev:  %.4f\n', std(true_matches_loglikelihoods_vector))
    fprintf(1, '\t\t-Inf false scores:    %.4f%%\n', 100*inf_false_scores_count/length(false_matches_loglikelihoods_vector))
    fprintf(1, '\t\tNon-inf false scores average:  %.4f\n', non_inf_false_scores_mean)
    fprintf(1, '\t\tNon-inf false scores std dev:  %.4f\n', non_inf_false_scores_std)
    true_matches_loglikelihoods_mean_matrix(mu_factor_index, sequences_length_index) = mean(true_matches_loglikelihoods_vector);
    true_matches_loglikelihoods_std_matrix(mu_factor_index, sequences_length_index) = std(true_matches_loglikelihoods_vector);
    inf_false_scores_count_matrix(mu_factor_index, sequences_length_index) = inf_false_scores_count/length(false_matches_loglikelihoods_vector);
    non_inf_false_scores_mean_matrix(mu_factor_index, sequences_length_index) = non_inf_false_scores_mean;
    non_inf_false_scores_std_matrix(mu_factor_index, sequences_length_index) = non_inf_false_scores_std;
    % Check whether classes are already separable. If so, go for next mu.
    if EER == 0
      fprintf(1, '\tTrue and false classes are already separable for this mu = %.1f with sequence length %i. Going for next mu.\n\n', mu, sequences_length);
      break
    endif
  endfor
  plot(sequences_length_vector, 100*(1-EER), [';\mu = ' num2str(mu) ';']);
  hold on;
endfor

xlabel('Sequence length');
ylabel('Balanced Accuracy (%)');
grid minor;
print('BalancedAccuracyPlots', '-dpdfcrop');

figure;
surf(sequences_length_vector, mu_factor_vector, 100*(1-EER));
xlabel('Arrival times sequence length');
ylabel('Source rate $\mu$ ($\lambda = 1$)');
zlabel('Attack balanced accuracy \%');
grid minor
axis([min(sequences_length_vector), max(sequences_length_vector), min(mu_factor_vector), max(mu_factor_vector), 50, 100]);
print('BalancedAccuracySurface', '-dpdflatex', '-S300,225');

